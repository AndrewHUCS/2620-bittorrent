import socket
import threading
import time
import random
import hashlib
import json

# === Configuration Constants ===
K = 20              # Maximum contacts per bucket (k-bucket size)
ALPHA = 3           # Concurrency factor for parallel requests
ID_LENGTH = 160     # Length of node IDs in bits
TIMEOUT = 2.0       # Socket timeout in seconds for RPC calls
BOOTSTRAP_RANGE = range(5000, 5101)  # Known DHT nodes ports on localhost

class Contact:
    """
    Represents a peer in the network.
    Stores its node ID (binary string) and UDP address.
    """
    def __init__(self, node_id, address):
        self.node_id = node_id
        self.address = address

class KademliaNode:
    """
    Main DHT node class: handles networking, routing table,
    bootstrapping into the network, and key-value storage RPCs.
    """
    def __init__(self, node_id, host, port):
        # Unique identifier for this node (160-bit string)
        self.node_id = node_id
        self.address = (host, port)

        # UDP socket bound to our address
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(self.address)

        # Routing table uses ping() callback for LRU eviction
        self.routing_table = RoutingTable(node_id, self.ping)

        # Local in-memory key->value store
        self.data_store = {}

        # Start background listener for incoming messages
        threading.Thread(target=self.listen, daemon=True).start()

    def listen(self):
        """
        Continuously receive UDP messages and dispatch to handler threads.
        """
        while True:
            data, addr = self.socket.recvfrom(4096)
            msg = deserialize(data)
            threading.Thread(
                target=self.handle_message,
                args=(msg, addr),
                daemon=True
            ).start()

    def handle_message(self, msg, addr):
        """
        Handle incoming RPCs:
        - FIND_NODE: return K closest contacts
        - PING: liveness check
        - STORE: save key/value locally
        - FIND_VALUE: return value or K closest nodes
        """
        mtype = msg.get('type')

        if mtype == 'FIND_NODE':
            # Respond with up to K closest contacts to target_id
            closest = self.routing_table.find_closest(msg['target_id'], K)
            resp = {'type': 'NODES', 'nodes': [(c.node_id, c.address) for c in closest]}
            self.socket.sendto(serialize(resp), addr)

        elif mtype == 'PING':
            # Simple liveness reply
            self.socket.sendto(serialize({'type': 'PONG'}), addr)

        elif mtype == 'STORE':
            # Store provided key/value pair locally
            key, value = msg['key'], msg['value']
            self.data_store[key] = value
            # Acknowledge storage
            self.socket.sendto(serialize({'type': 'STORED', 'key': key}), addr)

        elif mtype == 'FIND_VALUE':
            key = msg['key']
            if key in self.data_store:
                # Value found locally
                resp = {'type': 'VALUE', 'key': key, 'value': self.data_store[key]}
            else:
                # Otherwise, return K closest contacts for further lookup
                closest = self.routing_table.find_closest(msg['target_id'], K)
                resp = {'type': 'NODES', 'nodes': [(c.node_id, c.address) for c in closest]}
            self.socket.sendto(serialize(resp), addr)

    def bootstrap(self):
        """
        Bootstraps this node into the DHT by querying known nodes
        at 127.0.0.1:5000–5100, then performing a self FIND_NODE to populate
        the routing table with the K closest contacts to our own ID.
        """
        for port in BOOTSTRAP_RANGE:
            addr = ('127.0.0.1', port)
            try:
                # Send FIND_NODE for our own ID
                msg = {'type': 'FIND_NODE', 'target_id': self.node_id}
                self.socket.sendto(serialize(msg), addr)
                self.socket.settimeout(TIMEOUT)
                data, _ = self.socket.recvfrom(4096)
                resp = deserialize(data)
                if resp.get('type') == 'NODES':
                    for nid, caddr in resp['nodes']:
                        contact = Contact(nid, tuple(caddr))
                        self.routing_table.insert(contact)
            except socket.timeout:
                continue
        # Perform iterative lookup on our own ID to further populate table
        self.find_node(self.node_id)

    def find_node(self, target_id):
        """
        Public API: perform iterative FIND_NODE lookup.
        Returns list of up to K closest contacts to target_id.
        """
        return self._iterative_lookup(target_id, find_value=False)

    def get_value(self, key):
        """
        Public API: retrieve a value by key from the DHT.
        Hash key to 160-bit ID and perform FIND_VALUE.
        Returns the stored value or None if not found.
        """
        key_hash = sha1_bits(key)
        return self._iterative_lookup(key_hash, find_value=True, key=key)

    def store(self, key, value):
        """
        Public API: store a key/value pair in the K closest nodes in the overall network.
        Hash key to ID, perform FIND_NODE to discover K closest contacts, then send STORE.
        """
        key_hash = sha1_bits(key)
        closest = self.find_node(key_hash)
        for contact in closest:
            msg = {'type': 'STORE', 'key': key, 'value': value}
            self.socket.sendto(serialize(msg), contact.address)

    def _iterative_lookup(self, target_id, find_value=False, key=None):
        """
        Core iterative lookup loop for FIND_NODE or FIND_VALUE.
        - Maintains a shortlist of known contacts
        - Sends up to ALPHA parallel requests
        - Stops when no closer nodes are discovered
        """
        shortlist = self.routing_table.find_closest(target_id, K)
        contacted = set()
        previous = []

        while True:
            candidates = [c for c in shortlist if c.address not in contacted][:ALPHA]
            if not candidates:
                break

            lock, responses = threading.Lock(), []

            def query(contact):
                try:
                    msg_type = 'FIND_VALUE' if find_value else 'FIND_NODE'
                    msg = {'type': msg_type, 'target_id': target_id, 'key': key}
                    self.socket.sendto(serialize(msg), contact.address)
                    self.socket.settimeout(TIMEOUT)
                    data, _ = self.socket.recvfrom(4096)
                    resp = deserialize(data)
                    with lock:
                        responses.append((contact, resp))
                except socket.timeout:
                    pass
                finally:
                    contacted.add(contact.address)

            threads = [threading.Thread(target=query, args=(c,)) for c in candidates]
            for t in threads: t.start()
            for t in threads: t.join()

            for contact, resp in responses:
                self.routing_table.insert(contact)
                if find_value and resp.get('type') == 'VALUE':
                    return resp['value']
                if resp.get('type') == 'NODES':
                    for nid, caddr in resp['nodes']:
                        new_c = Contact(nid, tuple(caddr))
                        self.routing_table.insert(new_c)
                        if new_c not in shortlist:
                            shortlist.append(new_c)

            shortlist.sort(key=lambda c: xor_distance(c.node_id, target_id))
            shortlist = shortlist[:K]

            if previous and all(c in previous for c in shortlist):
                break
            previous = shortlist[:]

        return None if find_value else shortlist

    def ping(self, contact):
        """
        Liveness check: send PING and expect PONG.
        Used in LRU eviction to verify oldest contact.
        """
        try:
            self.socket.sendto(serialize({'type': 'PING'}), contact.address)
            self.socket.settimeout(TIMEOUT)
            data, _ = self.socket.recvfrom(4096)
            return deserialize(data).get('type') == 'PONG'
        except socket.timeout:
            return False

# === Routing Table Implementation ===
# Uses a lazy-splitting binary tree of buckets with LRU eviction

def int_from_bits(bit_str):
    return int(bit_str, 2)

class Bucket:
    """
    Represents a range of the ID space.
    Splits when full and covers our own ID.
    """
    def __init__(self, range_min, range_max, depth=0):
        self.range_min = range_min
        self.range_max = range_max
        self.contacts = []
        self.left = None
        self.right = None
        self.depth = depth

    def is_leaf(self):
        return self.left is None and self.right is None

    def contains(self, node_int):
        return self.range_min <= node_int <= self.range_max

    def split(self):
        mid = (self.range_min + self.range_max) // 2
        self.left = Bucket(self.range_min, mid, self.depth + 1)
        self.right = Bucket(mid + 1, self.range_max, self.depth + 1)
        for c in self.contacts:
            target = self.left if self.left.contains(int_from_bits(c.node_id)) else self.right
            target.contacts.append(c)
        self.contacts = []

    def get_all_contacts(self):
        if self.is_leaf():
            return list(self.contacts)
        return self.left.get_all_contacts() + self.right.get_all_contacts()

class RoutingTable:
    """
    Manages bucket tree: insertion with lazy splitting and LRU eviction.
    """
    def __init__(self, my_node_id, ping_callback):
        self.my_id = int_from_bits(my_node_id)
        self.ping = ping_callback
        self.root = Bucket(0, 2**ID_LENGTH - 1)

    def insert(self, contact):
        self._insert(self.root, contact, int_from_bits(contact.node_id))

    def _insert(self, bucket, contact, node_int):
        if not bucket.is_leaf():
            branch = bucket.left if bucket.left.contains(node_int) else bucket.right
            return self._insert(branch, contact, node_int)
        for i, c in enumerate(bucket.contacts):
            if c.node_id == contact.node_id:
                bucket.contacts.pop(i)
                bucket.contacts.append(c)
                return
        if len(bucket.contacts) < K:
            bucket.contacts.append(contact)
            return
        if bucket.contains(self.my_id):
            bucket.split()
            return self._insert(bucket, contact, node_int)
        oldest = bucket.contacts[0]
        if self.ping(oldest):
            bucket.contacts.pop(0)
            bucket.contacts.append(oldest)
        else:
            bucket.contacts.pop(0)
            bucket.contacts.append(contact)

    def find_closest(self, target_id, k):
        contacts = self.root.get_all_contacts()
        contacts.sort(key=lambda c: xor_distance(c.node_id, target_id))
        return contacts[:k]

# === Utility Functions ===

def sha1_bits(key):
    digest = hashlib.sha1(key.encode()).hexdigest()
    return bin(int(digest, 16))[2:].zfill(ID_LENGTH)

def xor_distance(a, b):
    return int(a, 2) ^ int(b, 2)

def serialize(msg):
    return json.dumps(msg).encode()

def deserialize(data):
    return json.loads(data.decode())

# === Example Usage ===
if __name__ == '__main__':
    # Create a new node and bootstrap into the existing DHT
    node = KademliaNode(sha1_bits('nodeA'), '127.0.0.1', 8468)
    node.bootstrap()
    # Now store and retrieve a value
    node.store('foo', 'bar')
    print('Retrieved:', node.get_value('foo'))
