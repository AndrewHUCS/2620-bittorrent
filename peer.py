import sys
import socket
import threading
import random
import time
import hashlib
import collections
from utils import bencode, bdecode, send, recv_all

info_hash    = hashlib.sha1(b"test_file").digest()  # hard-coded file name, can change to diff files to create diff swarms
peer_id      = b'PEER' + bytes(f"{random.randint(0, 999999):06d}", 'utf-8')
total_pieces = 10   # hard-coded number of pieces of file

own_pieces  = set()
known_peers = []    # list of dicts: { 'addr':(ip,port), 'pieces':set(...) }

# check what pieces the peer already has
for i in range(total_pieces):
    try:
        with open(f"piece_{i}.bin", "rb"):  # hard-coded file name 
            own_pieces.add(i)
    except FileNotFoundError:
        pass

def handle_peer(conn, addr):
    '''handles incoming connections from other peerds'''
    try:
        print(f"[PEER] connection from {addr}")

        # try to receive handshake
        raw = recv_all(conn)
        if not raw:
            print(f"[PEER] {addr} closed before handshake")
            return
        msg = bdecode(raw)
        if msg.get(b'type') != b'handshake':
            print(f"[PEER] expected handshake, got {msg}")
            return
        print(f"[PEER] handshake from {addr}")
        
        # respond to handshake
        send(conn, bencode({
            b'type':      b'handshake',
            b'info_hash': info_hash,
            b'peer_id':   peer_id
        }))

        # try to receive request
        raw = recv_all(conn)
        if not raw:
            print(f"[PEER] {addr} closed before request")
            return
        msg = bdecode(raw)
        if msg.get(b'type') == b'request':
            idx = int(msg[b'piece'])
            if idx in own_pieces:
                print(f"[PEER] sending piece {idx} to {addr}")
                data = open(f"piece_{idx}.bin", 'rb').read()
                send(conn, bencode({
                    b'type':  b'piece',
                    b'piece': str(idx).encode(),
                    b'data':  data
                }))
        else:
            print(f"[PEER] unexpected message: {msg}")

    except Exception:
        print(f"[ERROR] in handle_peer for {addr}:")
    finally:
        conn.close()
        print(f"[PEER] closed connection to {addr}")

def serve_peers(listen_sock):
    while True:
        conn, addr = listen_sock.accept()
        threading.Thread(target=handle_peer, args=(conn, addr), daemon=True).start()

def connect_tracker(tracker_addr, listen_port):
    global known_peers
    s = socket.socket()
    s.connect(tracker_addr)
    announce = {
        b'msg':       b'announce',
        b'info_hash': info_hash,
        b'peer_id':   peer_id,
        b'port':      str(listen_port).encode(),
        b'pieces':    [str(i).encode() for i in sorted(own_pieces)]
    }
    send(s, bencode(announce))
    resp = recv_all(s)
    info = bdecode(resp)

    new_list = []
    for ip_b, port_b, pieces_list in info.get(b'peers', []):
        ip   = ip_b.decode()
        prt  = int(port_b)
        plist = set(int(x) for x in pieces_list)
        new_list.append({'addr': (ip, prt), 'pieces': plist})
    known_peers = new_list
    s.close()

def notify_tracker_of_piece(tracker_addr, listen_port, piece_index):
    '''helper function to tell tracker whenever a new piece is acquired by current peer'''
    s = socket.socket()
    s.connect(tracker_addr)
    notify = {
        b'type':      b'has_piece',
        b'info_hash': info_hash,
        b'port':      str(listen_port).encode(),
        b'piece':     str(piece_index).encode()
    }
    send(s, bencode(notify))
    s.close()

def download_loop(tracker_addr, listen_port):
    '''constantly poll to download while not finished and upload to others'''
    done = False
    start_time = time.time()
    while True:
        connect_tracker(tracker_addr, listen_port)
        if len(own_pieces) < total_pieces:
            # rarest piece selection
            missing_pieces = set(range(total_pieces)) - own_pieces
            piece_counts = collections.defaultdict(int)
            for peer in known_peers:
                for piece in peer['pieces']:
                    if piece in missing_pieces:
                        piece_counts[piece] += 1
            
            # if haven't met peer with needed pieces, wait
            if not piece_counts:
                time.sleep(1)
                continue

            # pick random piece from pieces tied for rarest
            min_count = min(piece_counts.values())
            rarest_candidates = [p for p, count in piece_counts.items() if count == min_count]
            target = random.choice(rarest_candidates)

            suitable_peers = [peer for peer in known_peers if target in peer['pieces']]
            random.shuffle(suitable_peers)

            # download from randomly chosen peer that has piece
            for peer in suitable_peers:
                if target not in peer['pieces']:
                    continue

                ip, prt = peer['addr']
                try:
                    conn = socket.socket()
                    conn.connect((ip, prt))

                    # send handshake
                    send(conn, bencode({
                        b'type':      b'handshake',
                        b'info_hash': info_hash,
                        b'peer_id':   peer_id
                    }))
                    recv_all(conn)

                    # request piece
                    send(conn, bencode({
                        b'type':  b'request',
                        b'piece': str(target).encode()
                    }))

                    # obtain response from request and decode into piece of file
                    raw = recv_all(conn)
                    if not raw:
                        raise Exception("Empty response from peer")

                    msg = bdecode(raw)

                    if msg.get(b'type') == b'piece':
                        data = msg[b'data']
                        with open(f"piece_{target}.bin", "wb") as f:
                            f.write(data)
                        own_pieces.add(target)
                        print(f"Downloaded piece {target} from {ip}:{prt}")

                        # report to tracker
                        notify_tracker_of_piece(tracker_addr, listen_port, target)

                    conn.close()
                    break

                except Exception as e:
                    print(f"Error downloading piece {target} from {ip}:{prt} - {e}")
        elif not done:
            done = True
            end_time = time.time()
            duration = end_time - start_time
            print(f"\n Finished downloading all pieces in {duration:.2f} seconds.\n")

def main():
    if len(sys.argv) != 5:
        print("Usage: python peer.py <host> <port> <tracker_host> <tracker_port>")
        return
    host, port = sys.argv[1], int(sys.argv[2])
    tracker_addr = (sys.argv[3], int(sys.argv[4]))

    lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lsock.bind((host, port))
    lsock.listen()
    print(f"Peer listening on {host}:{port}")

    threading.Thread(target=serve_peers, args=(lsock,), daemon=True).start()
    threading.Thread(target=download_loop, args=(tracker_addr, port), daemon=True).start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Caught keyboard interrupt, exiting")

if __name__ == "__main__":
    main()
