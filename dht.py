import argparse
import hashlib
import json
import random
import socket
import threading
import time

# === Configuration Constants ===
K = 20  # Maximum contacts per bucket (k-bucket size)
ALPHA = 3  # Concurrency factor for parallel requests
ID_LENGTH = 160  # Length of node IDs in bits
TIMEOUT = 2.0  # Socket timeout in seconds for RPC calls
BOOTSTRAP_RANGE = range(50000, 50101)  # Bootstrap ports (127.0.0.1:50000–50100)


# === Utility Functions ===

def generate_node_id():
    """
    Generate a random 160-bit node ID as a binary string.
    """
    return bin(random.getrandbits(ID_LENGTH))[2:].zfill(ID_LENGTH)


def sha1_bits(key):
    """
    Hash a string to a 160-bit binary string using SHA-1.
    """
    digest = hashlib.sha1(key.encode()).hexdigest()
    return bin(int(digest, 16))[2:].zfill(ID_LENGTH)


def xor_distance(a, b):
    """
    XOR distance metric between two binary ID strings.
    """
    return int(a, 2) ^ int(b, 2)


def serialize(msg):
    """
    JSON-encode a message dictionary to bytes.
    """
    return json.dumps(msg).encode()


def deserialize(data):
    """
    Decode bytes to a JSON object (dict).
    """
    return json.loads(data.decode())


class Contact:
    """
    Represents a peer in the DHT network.
    Stores its node ID and UDP address.
    """

    def __init__(self, node_id, address):
        self.node_id = node_id
        self.address = address


class KademliaNode:
    """
    Kademlia DHT node: manages socket, routing table,
    bootstrapping, and key-value RPCs.
    """

    def __init__(self, host, port, node_id=None):
        # Assign or generate a 160-bit node ID
        self.node_id = node_id or generate_node_id()
        self.address = (host, port)
        print(f"Node ID: {self.node_id}, Address: {self.address}")

        # Create UDP socket with address/port reuse
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass

        # Bind socket
        try:
            self.socket.bind(self.address)
            print("Socket bound successfully.")
        except OSError as e:
            print(f"Error binding to {self.address}: {e}")
            raise SystemExit(1)

        # Ensure blocking mode
        self.socket.settimeout(None)

        # Initialize routing table with ping callback
        self.routing_table = RoutingTable(self.node_id, self.ping)
        print("Routing table initialized.")

        # In-memory key->value store
        self.data_store = {}

        # Start listener thread
        listener = threading.Thread(target=self.listen, daemon=True)
        listener.start()

    def listen(self):
        """
        Listen for incoming UDP messages and dispatch handlers.
        Ensure every sender is inserted into routing table.
        """
        import errno
        while True:
            try:
                data, addr = self.socket.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError as e:
                if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                    continue
                print(f"Listen socket error: {e}")
                continue

            try:
                msg = deserialize(data)
            except json.JSONDecodeError:
                continue

            # Update routing table with sender for freshness
            if 'node_id' in msg:
                self.routing_table.insert(Contact(msg['node_id'], addr))

            threading.Thread(
                target=self.handle_message,
                args=(msg, addr),
                daemon=True
            ).start()

    def handle_message(self, msg, addr):
        """
        Handle Kademlia RPCs: FIND_NODE, PING, STORE, FIND_VALUE.
        """
        mtype = msg.get('type')

        if mtype == 'FIND_NODE':
            closest = self.routing_table.find_closest(msg['target_id'], K)
            response = {
                'type': 'NODES',
                'nodes': [(c.node_id, c.address) for c in closest]
            }
            self.socket.sendto(serialize(response), addr)

        elif mtype == 'PING':
            self.socket.sendto(serialize({'type': 'PONG'}), addr)

        elif mtype == 'STORE':
            key, value = msg['key'], msg['value']
            self.data_store[key] = value
            self.socket.sendto(serialize({'type': 'STORED', 'key': key}), addr)

        elif mtype == 'FIND_VALUE':
            key = msg['key']
            if key in self.data_store:
                response = {'type': 'VALUE', 'key': key, 'value': self.data_store[key]}
            else:
                closest = self.routing_table.find_closest(msg['target_id'], K)
                response = {
                    'type': 'NODES',
                    'nodes': [(c.node_id, c.address) for c in closest]
                }
            self.socket.sendto(serialize(response), addr)

    def bootstrap(self):
        """
        Join the DHT by querying known bootstrap nodes once.
        Sends FIND_NODE to each without blocking; breaks out as soon as a peer is discovered.
        If no peers respond, starts the network standalone.
        """
        print("Bootstrap starting…")
        found_peer = False
        import errno
        for port in BOOTSTRAP_RANGE:
            if port == self.address[1]:
                continue
            addr = ('127.0.0.1', port)
            msg = {'type': 'FIND_NODE', 'target_id': self.node_id}
            self.socket.sendto(serialize(msg), addr)

            # Immediate non-blocking reply check
            self.socket.settimeout(0.0)
            try:
                data, _ = self.socket.recvfrom(4096)
                resp = deserialize(data)
                if resp.get('type') == 'NODES':
                    print(resp)
                    print(f"  Saw peer at {addr}, inserting contacts.")
                    for nid, caddr in resp['nodes']:
                        self.routing_table.insert(Contact(nid, tuple(caddr)))
                    found_peer = True
                    break
            except (BlockingIOError, socket.timeout):
                pass
            except OSError as e:
                if e.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                    raise
            finally:
                self.socket.settimeout(None)

        if found_peer:
            self.find_node(self.node_id)
            print("Bootstrap successful, routing table initialized.")
        else:
            print("No bootstrap peers found, starting standalone DHT.")

    def find_node(self, target_id):
        """Iterative FIND_NODE: return K closest contacts, including self."""
        contacts = self._iterative_lookup(target_id, find_value=False)
        # include self as a Contact and sort
        self_contact = Contact(self.node_id, self.address)
        all_contacts = contacts + [self_contact]
        all_contacts.sort(key=lambda c: xor_distance(c.node_id, target_id))
        return all_contacts[:K]

    def get_value(self, key):
        """Iterative FIND_VALUE: return value or None."""
        key_hash = sha1_bits(key)
        return self._iterative_lookup(key_hash, find_value=True, key=key)

    def store(self, key, value):
        """Store key/value locally and on K closest network nodes."""
        self.data_store[key] = value
        key_hash = sha1_bits(key)
        closest = self.find_node(key_hash)
        for c in closest:
            msg = {'type': 'STORE', 'key': key, 'value': value}
            self.socket.sendto(serialize(msg), c.address)

    def _iterative_lookup(self, target_id, find_value=False, key=None):
        """Core loop for FIND_NODE or FIND_VALUE."""
        shortlist = self.routing_table.find_closest(target_id, K)
        contacted = set()
        previous = []

        while True:
            candidates = [c for c in shortlist if c.address not in contacted][:ALPHA]
            if not candidates:
                break

            lock = threading.Lock()
            responses = []

            def query_node(contact):
                self.socket.settimeout(TIMEOUT)
                try:
                    rpc = 'FIND_VALUE' if find_value else 'FIND_NODE'
                    msg = {'type': rpc, 'target_id': target_id, 'key': key}
                    self.socket.sendto(serialize(msg), contact.address)
                    data, _ = self.socket.recvfrom(4096)
                    resp = deserialize(data)
                    with lock:
                        responses.append((contact, resp))
                except socket.timeout:
                    pass
                finally:
                    contacted.add(contact.address)
                    self.socket.settimeout(None)

            threads = []
            for c in candidates:
                t = threading.Thread(target=query_node, args=(c,))
                t.start()
                threads.append(t)
            for t in threads:
                t.join()

            for contact, resp in responses:
                self.routing_table.insert(contact)
                if find_value and resp.get('type') == 'VALUE':
                    return resp['value']
                if resp.get('type') == 'NODES':
                    for nid, caddr in resp['nodes']:
                        newc = Contact(nid, tuple(caddr))
                        self.routing_table.insert(newc)
                        if newc not in shortlist:
                            shortlist.append(newc)

            shortlist.sort(key=lambda c: xor_distance(c.node_id, target_id))
            shortlist = shortlist[:K]

            if previous and all(c in previous for c in shortlist):
                break
            previous = shortlist.copy()

        return None if find_value else shortlist

    def ping(self, contact):
        """Liveness check: send PING and await PONG."""
        self.socket.settimeout(TIMEOUT)
        try:
            self.socket.sendto(serialize({'type': 'PING'}), contact.address)
            data, _ = self.socket.recvfrom(4096)
            return deserialize(data).get('type') == 'PONG'
        except socket.timeout:
            return False
        finally:
            self.socket.settimeout(None)


class Bucket:
    """
    ID-space bucket with lazy split and LRU eviction.
    """
    def __init__(self, range_min, range_max, depth=0):
        self.range_min = range_min
        self.range_max = range_max
        self.contacts = []
        self.left = None
        self.right = None
        self.depth = depth

    def is_leaf(self):
        return self.left is None

    def contains(self, node_int):
        return self.range_min <= node_int <= self.range_max

    def split(self):
        mid = (self.range_min + self.range_max) // 2
        self.left = Bucket(self.range_min, mid, self.depth + 1)
        self.right = Bucket(mid + 1, self.range_max, self.depth + 1)
        for c in self.contacts:
            cid = int(c.node_id, 2)
            target = self.left if self.left.contains(cid) else self.right
            target.contacts.append(c)
        self.contacts.clear()

    def get_all_contacts(self):
        if self.is_leaf():
            return list(self.contacts)
        return self.left.get_all_contacts() + self.right.get_all_contacts()


class RoutingTable:
    """
    Binary tree of buckets with lazy split and LRU eviction.
    """
    def __init__(self, my_node_id, ping_callback):
        self.my_id = int(my_node_id, 2)
        self.ping = ping_callback
        self.root = Bucket(0, 2**ID_LENGTH - 1)

    def insert(self, contact):
        node_int = int(contact.node_id, 2)
        self._insert(self.root, contact, node_int)

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

        oldest = bucket.contacts.pop(0)
        if self.ping(oldest):
            bucket.contacts.append(oldest)
        else:
            bucket.contacts.append(contact)

    def find_closest(self, target_id, k):
        contacts = self.root.get_all_contacts()
        contacts.sort(key=lambda c: xor_distance(c.node_id, target_id))
        return contacts[:k]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Run a Kademlia DHT node')
    parser.add_argument('--host', default='127.0.0.1', help='IP address to bind')
    parser.add_argument('--port', type=int, required=True, help='UDP port to bind')
    parser.add_argument('--bootstrap', action='store_true', help='Perform bootstrap to known nodes')
    parser.add_argument('--test', action='store_true', help='Run periodic store/get test')
    args = parser.parse_args()

    print("Creating node…")
    node = KademliaNode(args.host, args.port)
    print(f'Node running at {node.address} with ID {node.node_id}')

    if args.bootstrap:
        node.bootstrap()
        print('Bootstrap completed.')

    def periodic_store_get():
        """
        Periodically store and retrieve random keys.
        """
        while True:
            key = f'key{random.randint(0, 1000)}'
            value = f'val{random.randint(0, 1000)}'
            node.store(key, value)
            print(f'[{node.address}] Stored {key} -> {value}')
            time.sleep(random.uniform(1, 3))

            result = node.get_value(key)
            print(f'[{node.address}] Retrieved {key} -> {result}')
            time.sleep(random.uniform(1, 3))

    if args.test:
        threading.Thread(target=periodic_store_get, daemon=True).start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print('Node shutting down.')
