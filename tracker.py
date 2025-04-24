import sys
import socket
import threading
from utils import bencode, bdecode, send, recv_all

torrents = {}

def handle_client(conn, addr):
    '''handles incoming connections from peers'''
    try:
        raw = recv_all(conn)
        msg = bdecode(raw)
        ip = addr[0]

        # receives announcement from peers containing
        if msg.get(b'msg') == b'announce':
            info_hash = msg[b'info_hash']
            port = int(msg[b'port'])
            peer_pieces = set(int(x) for x in msg.get(b'pieces', []))

            torrents.setdefault(info_hash, {})
            torrents[info_hash][(ip, port)] = peer_pieces

            peers_list = []
            for (pip, pport), pset in torrents[info_hash].items():
                if (pip, pport) != (ip, port):
                    peers_list.append([
                        pip.encode(),
                        str(pport).encode(),
                        [str(i).encode() for i in sorted(pset)]
                    ])

            send(conn, bencode({b'peers': peers_list}))

        # receives message from peer about new pieces it obtains
        elif msg.get(b'type') == b'has_piece':
            info_hash = msg[b'info_hash']
            port = int(msg[b'port'])
            idx  = int(msg[b'piece'])
            torrents.setdefault(info_hash, {})
            torrents[info_hash].setdefault((ip, port), set()).add(idx)

    except Exception as e:
        print("Tracker error:", e)
    finally:
        conn.close()

def main():
    if len(sys.argv) != 3:
        print("Usage: python tracker.py <host> <port>")
        return

    host, port = sys.argv[1], int(sys.argv[2])
    lsock = socket.socket()
    lsock.bind((host, port))
    lsock.listen()
    print(f"Tracker listening on {host}:{port}")

    try:
        while True:
            conn, addr = lsock.accept()
            threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()
    except KeyboardInterrupt:
        print("Caught keyboard interrupt, exiting")
    finally:
        lsock.close()

if __name__ == "__main__":
    main()
