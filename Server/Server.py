import socket
import os
import threading
from socket import AF_INET, SOCK_STREAM
from socket import SOL_SOCKET, SO_REUSEADDR
from struct import *
from config import *

log_user_socket = {}
text_user_socket = {}
text_lock = {}
voice_user_socket = {}
voice_lock = {}
file_user_socket = {}
file_lock = {}
offline_file = {}
file_transfer_willing = {}
file_transfer_done = {}
file_transfer_progress = {}


def log_socket_connect_handler(user: socket.socket) -> None:
    request = user.recv(21)
    username, password = request.replace(b'\x00', b'').decode().split(SEPERATOR)
    if username not in AUTHORIZED_USER:
        response = 'Username Error!'
        user.send(response.encode())
        return
    if password != AUTHORIZED_USER[username]:
        response = 'Password Error!'
        user.send(response.encode())
        return
    response = 'Login Success!!'
    user.send(response.encode())

    if not log_user_socket:
        online_info = pack('50s', 'Nobody Online!'.encode())
        user.send(online_info)
    else:
        online_user = [username for username in log_user_socket.keys()]
        online_info = ' '.join(online_user) + ' Online!'
        online_info = pack('50s', online_info.encode())
        user.send(online_info)

    for soc in log_user_socket.values():
        info = '{} is online!!'.format(username)
        info = pack('22s', info.encode())
        soc.send(info)
    log_user_socket[username] = user

    while True:
        request = user.recv(6)
        if request.decode() == 'logout':
            for soc in log_user_socket.values():
                if soc == user:
                    continue
                info = '{} is offline!'.format(username)
                info = pack('22s', info.encode())
                soc.send(info)

            user.close()
            text_user_socket[username].close()
            voice_user_socket[username].close()
            file_user_socket[username].close()

            del log_user_socket[username]
            del text_lock[text_user_socket[username]]
            del text_user_socket[username]
            del voice_lock[voice_user_socket[username]]
            del voice_user_socket[username]
            del file_lock[file_user_socket[username]]
            del file_user_socket[username]


def log_socket_handler(soc: socket.socket) -> None:
    while True:
        conn, addr = soc.accept()
        print('Log Socket receive connect from {}'.format(addr))
        threading.Thread(target=log_socket_connect_handler, args=(conn,)).start()


def text_socket_forward_handler(user: socket.socket, sender: str, text_body: bytes) -> None:
    text_lock[user].acquire()

    text_body_length = len(text_body)
    text_head = pack('15sB', sender.encode(), text_body_length)
    user.send(text_head)
    user.send(text_body)

    text_lock[user].release()


def text_socket_connect_handler(user: socket.socket) -> None:
    username = user.recv(calcsize('10s')).replace(b'\x00', b'').decode()
    text_user_socket[username] = user
    text_lock[user] = threading.Lock()

    while True:
        text_head = user.recv(calcsize('10sB'))
        if len(text_head) != calcsize('10sB'):
            continue
        receiver, text_body_length = unpack('10sB', text_head)
        receiver = receiver.replace(b'\x00', b'').decode()
        sender = username

        text_body = user.recv(text_body_length)

        if receiver == 'All':
            sender = '(All)' + sender
            for soc in text_user_socket.values():
                if soc == user:
                    continue
                threading.Thread(target=text_socket_forward_handler, args=(soc, sender, text_body)).start()
        else:
            recv = text_user_socket[receiver]
            threading.Thread(target=text_socket_forward_handler, args=(recv, sender, text_body)).start()


def text_socket_handler(soc: socket.socket) -> None:
    while True:
        conn, addr = soc.accept()
        print('Text Socket receive connect from {}'.format(addr))
        threading.Thread(target=text_socket_connect_handler, args=(conn,)).start()


def voice_socket_forward_handler(user: socket.socket, sender: str, file_path: str) -> None:
    voice_lock[user].acquire()

    file_name = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)
    message = SEPERATOR.join([sender, file_name])
    file_head = pack('80sL', message.encode(), file_size)
    user.send(file_head)

    with open(file_path, 'rb') as f:
        while True:
            bytes_read = f.read(BLOCK)
            if not bytes_read:
                break
            user.send(bytes_read)
        f.close()

    voice_lock[user].release()


def voice_socket_connect_handler(user: socket.socket) -> None:
    username = user.recv(calcsize('10s')).replace(b'\x00', b'').decode()
    voice_user_socket[username] = user
    voice_lock[user] = threading.Lock()

    while True:
        file_head = user.recv(calcsize('75sL'))
        if len(file_head) != calcsize('75sL'):
            continue
        message, file_size = unpack('75sL', file_head)
        receiver, file_name = message.replace(b'\x00', b'').decode().split(SEPERATOR)
        sender = username
        file_path = os.getcwd() + '/voice/{}/{}/'.format(sender, receiver) + file_name

        with open(file_path, 'wb') as f:
            bytes_recv = 0
            while bytes_recv < file_size:
                if file_size - bytes_recv < BLOCK:
                    bytes_read = user.recv(file_size - bytes_recv)
                else:
                    bytes_read = user.recv(BLOCK)
                f.write(bytes_read)
                bytes_recv = bytes_recv + len(bytes_read)
            f.close()

        if receiver == 'All':
            sender = '(All)' + sender
            for soc in voice_user_socket.values():
                if soc == user:
                    continue
                threading.Thread(target=voice_socket_forward_handler, args=(soc, sender, file_path)).start()
        else:
            recv = voice_user_socket[receiver]
            threading.Thread(target=voice_socket_forward_handler, args=(recv, sender, file_path)).start()


def voice_socket_handler(soc: socket.socket) -> None:
    while True:
        conn, addr = soc.accept()
        print('Voice Socket receive connect from {}'.format(addr))
        threading.Thread(target=voice_socket_connect_handler, args=(conn,)).start()


def file_transfer_continue(user: socket.socket) -> None:
    file_lock[user].acquire()

    sender, file_path, count = file_transfer_progress[user][-1]
    file_name = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)
    message = SEPERATOR.join([sender, file_name])
    file_head = pack('80sL', message.encode(), file_size)
    user.send(file_head)

    with open(file_path, 'rb') as f:
        f.seek(BLOCK * count, 0)
        while True:
            bytes_read = f.read(BLOCK)
            if not bytes_read:
                break
            user.send(bytes_read)
        f.close()
    del file_transfer_progress[user][-1]

    file_lock[user].release()


def file_socket_forward_handler(user: socket.socket, sender: str, file_path: str) -> None:
    file_lock[user].acquire()

    file_transfer_willing[user] = True
    file_transfer_done[user] = False

    file_name = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)
    message = SEPERATOR.join([sender, file_name])
    file_head = pack('80sL', message.encode(), file_size)
    user.send(file_head)

    with open(file_path, 'rb') as f:
        count = 0
        while True:
            bytes_read = f.read(BLOCK)
            if not file_transfer_willing[user]:
                file_transfer_progress.setdefault(user, [])
                file_transfer_progress[user].append([sender, file_path, count])
                print('Save the progress {}'.format(file_transfer_progress))
                break
            if not bytes_read:
                file_transfer_done[user] = True
                break
            user.send(bytes_read)
            count = count + 1
        f.close()

    file_lock[user].release()


def file_socket_connect_handler(user: socket.socket) -> None:
    username = user.recv(calcsize('10s')).replace(b'\x00', b'').decode()
    file_user_socket[username] = user
    file_lock[user] = threading.Lock()

    if username in offline_file.keys():
        for i in range(len(offline_file[username])):
            sender, file_path = offline_file[username][i]
            file_size = os.path.getsize(file_path)
            file_name = os.path.basename(file_path)
            message = SEPERATOR.join([sender, file_name])
            file_head = pack('80sL', message.encode(), file_size)
            user.send(file_head)

            with open(file_path, 'rb') as f:
                while True:
                    bytes_read = f.read(BLOCK)
                    if not bytes_read:
                        break
                    user.send(bytes_read)
                f.close()

    while True:
        file_head = user.recv(calcsize('75sL'))
        if len(file_head) != calcsize('75sL'):
            try:
                if file_head.decode() == 'continue':
                    print('File transferring continue!')
                    threading.Thread(target=file_transfer_continue, args=(user,)).start()
                elif file_head.decode() == 'stop':
                    if file_transfer_done[user] is False:
                        file_transfer_willing[user] = False
                        print('File transferring stop!')
                continue
            except:
                print('Bad value of file_head')
                continue
        message, file_size = unpack('75sL', file_head)
        receiver, file_name = message.replace(b'\x00', b'').decode().split(SEPERATOR)
        sender = username
        file_path = os.getcwd() + '/file/{}/{}/'.format(sender, receiver) + file_name

        with open(file_path, 'wb') as f:
            bytes_recv = 0
            while bytes_recv < file_size:
                if file_size - bytes_recv < BLOCK:
                    bytes_read = user.recv(file_size - bytes_recv)
                else:
                    bytes_read = user.recv(BLOCK)
                f.write(bytes_read)
                bytes_recv = bytes_recv + len(bytes_read)
            f.close()

        if receiver == 'All':
            sender = '(All)' + sender
            for soc in file_user_socket.values():
                if soc == user:
                    continue
                threading.Thread(target=file_socket_forward_handler, args=(soc, sender, file_path)).start()
        else:
            if receiver not in file_user_socket.keys():
                offline_file.setdefault(receiver, [])
                offline_file[receiver].append([sender, file_path])
                print('{} is offline! Save the file as offline-file!'.format(receiver))
                continue
            recv = file_user_socket[receiver]
            threading.Thread(target=file_socket_forward_handler, args=(recv, sender, file_path)).start()


def file_socket_handler(soc: socket.socket) -> None:
    while True:
        conn, addr = soc.accept()
        print('File Socket receive connect from {}'.format(addr))
        threading.Thread(target=file_socket_connect_handler, args=(conn,)).start()


def make_directory() -> None:
    for sender in AUTHORIZED_USER:
        voice_path = os.getcwd() + '/voice/' + sender + '/'
        file_path = os.getcwd() + '/file/' + sender + '/'
        os.makedirs(voice_path + 'All', exist_ok=True)
        os.makedirs(file_path + 'All', exist_ok=True)
        for receiver in AUTHORIZED_USER:
            if receiver != sender:
                os.makedirs(voice_path + receiver, exist_ok=True)
                os.makedirs(file_path + receiver, exist_ok=True)


if __name__ == '__main__':
    make_directory()

    log_socket = socket.socket(family=AF_INET, type=SOCK_STREAM,
                               proto=0, fileno=None)
    log_socket.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
    log_socket.bind((IP, LOG_PORT))
    log_socket.listen(MAX_SESSION)

    text_socket = socket.socket(family=AF_INET, type=SOCK_STREAM,
                                proto=0, fileno=None)
    text_socket.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
    text_socket.bind((IP, TEXT_PORT))
    text_socket.listen(MAX_SESSION)

    voice_socket = socket.socket(family=AF_INET, type=SOCK_STREAM,
                                 proto=0, fileno=None)
    voice_socket.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
    voice_socket.bind((IP, VOICE_PORT))
    voice_socket.listen(MAX_SESSION)

    file_socket = socket.socket(family=AF_INET, type=SOCK_STREAM,
                                proto=0, fileno=None)
    file_socket.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
    file_socket.bind((IP, FILE_PORT))
    file_socket.listen(MAX_SESSION)

    threading.Thread(target=log_socket_handler, args=(log_socket,)).start()
    threading.Thread(target=text_socket_handler, args=(text_socket,)).start()
    threading.Thread(target=voice_socket_handler, args=(voice_socket,)).start()
    threading.Thread(target=file_socket_handler, args=(file_socket,)).start()
