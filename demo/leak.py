import time

chunks = []
for i in range(10):
    chunks.append(bytearray(15 * 1024 * 1024))
    time.sleep(4)
