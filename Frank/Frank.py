import socket
import threading
import wave
import tqdm
import time
import os
import getpass
import signal
from socket import AF_INET, SOCK_STREAM
from struct import *
from config import *

username = str()
text_lock = threading.Lock()
voice_lock = threading.Lock()
file_lock = threading.Lock()
file_transfer_willing = bool()
file_socket_sigint = socket.socket()


def sigquit_handler(signum, frame) -> None:
    print('Catch SigQuit!')
    global file_transfer_willing
    file_transfer_willing = False
    file_socket_sigint.send('stop'.encode())


def log_socket_recv_handler(server: socket.socket) -> None:
    while True:
        change = server.recv(calcsize('22s'))
        change = change.replace(b'\x00', b'').decode()
        print(change)


def text_socket_recv_handler(server: socket.socket) -> None:
    while True:
        text_head = server.recv(calcsize('15sB'))
        if len(text_head) != calcsize('15sB'):
            continue
        sender, text_body_length = unpack('15sB', text_head)
        sender = sender.replace(b'\x00', b'').decode()
        text_body = server.recv(text_body_length).decode()
        print('{}: {}'.format(sender, text_body))


def voice_socket_recv_handler(server: socket.socket) -> None:
    while True:
        file_head = server.recv(calcsize('80sL'))
        if len(file_head) != calcsize('80sL'):
            continue
        message, file_size = unpack('80sL', file_head)
        sender, file_name = message.replace(b'\x00', b'').decode().split(SEPERATOR)
        file_path = os.getcwd() + '/voice_recv/{}/'.format(sender) + file_name
        with open(file_path, 'wb') as f:
            bytes_recv = 0
            while bytes_recv < file_size:
                if file_size - bytes_recv < BLOCK:
                    bytes_read = server.recv(file_size - bytes_recv)
                else:
                    bytes_read = server.recv(BLOCK)
                f.write(bytes_read)
                bytes_recv = bytes_recv + len(bytes_read)
            f.close()

        print('{}: voice ......'.format(sender))
        wf = wave.open(file_path, 'rb')
        p = pyaudio.PyAudio()
        stream = p.open(format=p.get_format_from_width(wf.getsampwidth()),
                        channels=wf.getnchannels(),
                        rate=wf.getframerate(),
                        output=True)
        data = wf.readframes(CHUNK)
        while data != '':
            stream.write(data)
            data = wf.readframes(CHUNK)
        stream.stop_stream()
        stream.close()
        p.terminate()


def file_socket_recv_handler(server: socket.socket) -> None:
    global file_transfer_willing
    while True:
        file_head = server.recv(calcsize('80sL'))
        if len(file_head) != calcsize('80sL'):
            continue
        message, file_size = unpack('80sL', file_head)
        sender, file_name = message.replace(b'\x00', b'').decode().split(SEPERATOR)
        file_path = os.getcwd() + '/file_recv/{}/'.format(sender) + file_name

        if os.path.exists(file_path):
            cur_size = os.path.getsize(file_path)
            remain_size = file_size - cur_size
            print('Already receive {} bytes, Remain {} bytes.'.format(cur_size, remain_size))
            progress = tqdm.tqdm(range(remain_size), f"Continue Receiving {file_name} From {sender}",
                                 unit="B", unit_scale=True, unit_divisor=1024)
            with open(file_path, 'ab') as f:
                bytes_recv = 0
                while bytes_recv < remain_size:
                    if remain_size - bytes_recv < BLOCK:
                        bytes_read = server.recv(remain_size - bytes_recv)
                    else:
                        bytes_read = server.recv(BLOCK)
                    f.write(bytes_read)
                    bytes_recv = bytes_recv + len(bytes_read)
                    progress.update(len(bytes_read))
                f.close()
                progress.close()
            continue

        file_transfer_willing = True
        progress = tqdm.tqdm(range(file_size), f"Receiving {file_name} From {sender}",
                             unit="B", unit_scale=True, unit_divisor=1024)
        with open(file_path, 'wb') as f:
            bytes_recv = 0
            while bytes_recv < file_size:
                if not file_transfer_willing:
                    print('File transferring stop!')
                    while True:
                        bytes_read = server.recv(BLOCK)
                        if FILE_TRANSFER_STOP_SIGN in bytes_read:
                            bytes_read = bytes_read.replace(FILE_TRANSFER_STOP_SIGN, b'')
                            f.write(bytes_read)
                            break
                        f.write(bytes_read)
                        progress.update(len(bytes_read))
                    progress.close()
                    break
                if file_size - bytes_recv < BLOCK:
                    bytes_read = server.recv(file_size - bytes_recv)
                else:
                    bytes_read = server.recv(BLOCK)
                f.write(bytes_read)
                bytes_recv = bytes_recv + len(bytes_read)
                progress.update(len(bytes_read))
            f.close()
            progress.close()


def text_socket_send_handler(server: socket.socket, inp: list) -> None:
    text_lock.acquire()

    receiver = inp[1]
    content = inp[2]
    text_head = pack('10sB', receiver.encode(), len(content.encode()))
    server.send(text_head)

    text_body = content.encode()
    server.send(text_body)

    text_lock.release()


def voice_socket_send_handler(server: socket.socket, inp: list) -> None:
    p = pyaudio.PyAudio()
    stream = p.open(format=FORMAT, channels=CHANNELS,
                    rate=RATE, input=True, frames_per_buffer=CHUNK)

    print("Recording Start! 5 seconds.")
    frames = []
    for i in range(0, int(RATE / CHUNK * RECORD_SECONDS)):
        data = stream.read(CHUNK)
        frames.append(data)
    print("Recording Finish!")

    stream.stop_stream()
    stream.close()
    p.terminate()

    receiver = inp[1]
    file_path = os.getcwd() + '/voice_send/{}/'.format(receiver) + str(int(time.time())) + '.wav'
    wf = wave.open(file_path, 'wb')
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(p.get_sample_size(FORMAT))
    wf.setframerate(RATE)
    wf.writeframes(b''.join(frames))
    wf.close()

    voice_lock.acquire()

    file_name = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)
    message = SEPERATOR.join([receiver, file_name])
    file_head = pack('75sL', message.encode(), file_size)
    server.send(file_head)

    with open(file_path, 'rb') as f:
        while True:
            bytes_read = f.read(BLOCK)
            if not bytes_read:
                break
            server.send(bytes_read)
        f.close()

    voice_lock.release()


def file_socket_send_handler(server: socket.socket, inp: list) -> None:
    file_lock.acquire()

    receiver = inp[1]
    file_path = inp[2]
    file_name = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)
    message = SEPERATOR.join([receiver, file_name])
    file_head = pack('75sL', message.encode(), file_size)
    server.send(file_head)

    progress = tqdm.tqdm(range(file_size), f"Sending {file_name}",
                         unit="B", unit_scale=True, unit_divisor=1024)
    with open(file_path, 'rb') as f:
        while True:
            bytes_read = f.read(BLOCK)
            if not bytes_read:
                break
            server.send(bytes_read)
            progress.update(len(bytes_read))
        f.close()
        progress.close()

    file_lock.release()


def login_handler(server: socket.socket) -> bool:
    global username
    username = str(input('Username:'))
    password = getpass.getpass(prompt='Password: ')
    # password = str(input('Password:'))
    request = SEPERATOR.join([username, password])
    request = pack('21s', request.encode())
    server.send(request)

    response = log_socket.recv(15).decode()
    if response != 'Login Success!!':
        reason = log_socket.recv(15).decode()
        print(reason)
        return False

    print(response)

    online_info = log_socket.recv(calcsize('50s'))
    online_info, = unpack('50s', online_info)
    online_info = online_info.replace(b'\x00', b'').decode()
    print(online_info)
    return True


def make_directory(cur_user: str) -> bool:
    USER = ['Alice', 'Bob', 'Frank']
    USER.remove(cur_user)

    path = os.getcwd() + '/voice_send/' + 'All'
    os.makedirs(path, exist_ok=True)
    for user in USER:
        path = os.getcwd() + '/voice_send/' + user
        os.makedirs(path, exist_ok=True)
        path = os.getcwd() + '/voice_recv/' + user
        os.makedirs(path, exist_ok=True)
        path = os.getcwd() + '/voice_recv/' + '(All)' + user
        os.makedirs(path, exist_ok=True)
        path = os.getcwd() + '/file_recv/' + user
        os.makedirs(path, exist_ok=True)
        path = os.getcwd() + '/file_recv/' + '(All)' + user
        os.makedirs(path, exist_ok=True)
    return True


if __name__ == '__main__':
    if not make_directory('Frank'):
        print('Directory Make Error!')
        exit(1)

    signal.signal(signal.SIGQUIT, sigquit_handler)

    log_socket = socket.socket(family=AF_INET, type=SOCK_STREAM,
                               proto=0, fileno=None)
    log_socket.bind((IP, LOG_PORT))

    text_socket = socket.socket(family=AF_INET, type=SOCK_STREAM,
                                proto=0, fileno=None)
    text_socket.bind((IP, TEXT_PORT))

    voice_socket = socket.socket(family=AF_INET, type=SOCK_STREAM,
                                 proto=0, fileno=None)
    voice_socket.bind((IP, VOICE_PORT))

    file_socket = socket.socket(family=AF_INET, type=SOCK_STREAM,
                                proto=0, fileno=None)
    file_socket.bind((IP, FILE_PORT))
    file_socket_sigint = file_socket

    log_socket.connect((SERVER_IP, SERVER_LOG_PORT))
    if not login_handler(log_socket):
        exit(1)

    text_socket.connect((SERVER_IP, SERVER_TEXT_PORT))
    info = pack('10s', username.encode())
    text_socket.send(info)
    voice_socket.connect((SERVER_IP, SERVER_VOICE_PORT))
    voice_socket.send(info)
    file_socket.connect((SERVER_IP, SERVER_FILE_PORT))
    file_socket.send(info)

    threading.Thread(target=log_socket_recv_handler, args=(log_socket,)).start()
    threading.Thread(target=text_socket_recv_handler, args=(text_socket,)).start()
    threading.Thread(target=voice_socket_recv_handler, args=(voice_socket,)).start()
    threading.Thread(target=file_socket_recv_handler, args=(file_socket,)).start()

    while True:
        inp = input().split(' ', 2)
        if inp[0] == 'text':
            threading.Thread(target=text_socket_send_handler, args=(text_socket, inp)).start()
        elif inp[0] == 'voice':
            threading.Thread(target=voice_socket_send_handler, args=(voice_socket, inp)).start()
        elif inp[0] == 'file':
            threading.Thread(target=file_socket_send_handler, args=(file_socket, inp)).start()
        elif inp[0] == 'logout':
            log_socket.send(inp[0].encode())
        elif inp[0] == 'continue':
            file_socket.send(inp[0].encode())
        else:
            pass
