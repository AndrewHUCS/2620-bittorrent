import os

def generate_large_pieces(piece_count=10, size_mb=100):
    for i in range(piece_count):
        filename = f"peer1/piece_{i}.bin"
        print(f"Creating {filename} ({size_mb}MB)...")
        with open(filename, "wb") as f:
            f.write(os.urandom(size_mb * 1024 * 1024))  # random binary content

if __name__ == "__main__":
    generate_large_pieces()