import struct

def bencode(data):
    '''encode data into b-encoded output'''
    if isinstance(data, int):
        return b'i' + str(data).encode() + b'e'
    elif isinstance(data, bytes):
        return str(len(data)).encode() + b':' + data
    elif isinstance(data, str):
        raw = data.encode()
        return str(len(raw)).encode() + b':' + raw
    elif isinstance(data, list):
        return b'l' + b''.join(bencode(item) for item in data) + b'e'
    elif isinstance(data, dict):
        items = sorted(data.items())
        return b'd' + b''.join(bencode(k) + bencode(v) for k, v in items) + b'e'
    else:
        raise TypeError(f"Type {type(data)} not supported")

def bdecode(data):
    '''decode data from b-encoded input'''
    index = 0
    stack = []
    while index < len(data):
        token = data[index:index+1]
        if token == b'i':
            end = data.index(b'e', index)
            val = int(data[index+1:end])
            stack.append(val)
            index = end + 1
        elif token.isdigit():
            colon = data.index(b':', index)
            length = int(data[index:colon])
            start = colon + 1
            val = data[start:start+length]
            stack.append(val)
            index = start + length
        elif token == b'l':
            stack.append('l')
            index += 1
        elif token == b'd':
            stack.append('d')
            index += 1
        elif token == b'e':
            temp = []
            while stack and stack[-1] not in ('l', 'd'):
                temp.append(stack.pop())
            kind = stack.pop()
            if kind == 'l':
                stack.append(list(reversed(temp)))
            else:
                d = {}
                temp = list(reversed(temp))
                for i in range(0, len(temp), 2):
                    d[temp[i]] = temp[i+1]
                stack.append(d)
            index += 1
        else:
            raise ValueError(f"Invalid token at {index}")
    return stack[0]

def send(sock, message):
    """send message"""
    size = struct.pack('!I', len(message))
    sock.sendall(size + message)

def recv_helper(sock, n):
    """helper function to receive the first n bytes of a message"""
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return buf
        buf += chunk
    return buf

def recv_all(sock):
    """receive a entire message"""
    hdr = recv_helper(sock, 4)
    if not hdr:
        return b''
    length = struct.unpack('!I', hdr)[0]
    return recv_helper(sock, length)
