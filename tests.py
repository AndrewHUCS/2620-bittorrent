import socket
import threading
import os
import traceback

from utils import send, recv_all, bencode, bdecode
import peer
import tracker

# create fake server for testing
def fake_server(receive_func, port=0):
    server = socket.socket()
    server.bind(('localhost', port))
    server.listen(1)
    actual_port = server.getsockname()[1]

    def server_thread():
        try:
            conn, addr = server.accept()
            receive_func(conn, addr)
        except Exception as e:
            print(f"[SERVER ERROR]: {e}")
        finally:
            server.close()

    threading.Thread(target=server_thread, daemon=True).start()
    return actual_port

def test_bencode_bdecode():
    original = {b'hello': b'world', b'test': b'2620'}
    encoded = bencode(original)
    decoded = bdecode(encoded)
    assert decoded == original
    print("test_bencode_bdecode PASSED")

def test_send_recv_all():
    message = b"test message"

    def server_side(conn, addr):
        received = recv_all(conn)
        assert received == message
        conn.close()

    port = fake_server(server_side)

    client = socket.socket()
    client.connect(('localhost', port))
    send(client, message)
    client.close()

    print("test_send_recv_all PASSED")

def setup_piece_files():
    for i in range(4):
        with open(f"piece_{i}.bin", "wb") as f:
            f.write(b"x" * 100)

def cleanup_piece_files():
    for i in range(4):
        try:
            os.remove(f"piece_{i}.bin")
        except FileNotFoundError:
            pass

def test_handle_peer_handshake():
    setup_piece_files()

    port = fake_server(peer.handle_peer)

    client = socket.socket()
    client.connect(('localhost', port))
    send(client, bencode({b'type': b'handshake'}))
    resp = recv_all(client)
    decoded = bdecode(resp)
    assert decoded[b'type'] == b'handshake'
    client.close()

    cleanup_piece_files()
    print("test_handle_peer_handshake PASSED")

def test_tracker():
    port = fake_server(tracker.handle_client)

    client = socket.socket()
    client.connect(('localhost', port))

    announce = {
        b'msg': b'announce',
        b'info_hash': b'test_hash',
        b'peer_id': b'PEER123',
        b'port': b'1234',
        b'pieces': [b'0', b'1']
    }

    send(client, bencode(announce))
    resp = recv_all(client)
    decoded = bdecode(resp)

    assert isinstance(decoded.get(b'peers', []), list)
    client.close()

    print("test_tracker PASSED")

def main():
    try:
        test_bencode_bdecode()
    except Exception:
        print("test_bencode_bdecode FAILED")
        traceback.print_exc()

    try:
        test_send_recv_all()
    except Exception:
        print("test_send_recv_all FAILED")
        traceback.print_exc()

    try:
        test_handle_peer_handshake()
    except Exception:
        print("test_handle_peer_handshake FAILED")
        traceback.print_exc()

    try:
        test_tracker()
    except Exception:
        print("test_tracker FAILED")
        traceback.print_exc()

if __name__ == "__main__":
    main()
