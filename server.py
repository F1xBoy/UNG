import os
from flask import Flask, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import random
import string

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

active_rooms = {}

def generate_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))

@socketio.on('create_room')
def on_create(data):
    sid = request.sid
    # Сервер больше не лепит теги, берет чистый ник от клиента
    nick = data.get('nickname', 'Аноним').strip()
    
    code = generate_code()
    while code in active_rooms:
        code = generate_code()
        
    join_room(code)
    active_rooms[code] = {'players': [sid], 'nicks': {sid: nick}}
    
    emit('room_created', {'code': code})

@socketio.on('join_room')
def on_join(data):
    sid = request.sid
    code = data.get('code', '').strip().upper()
    nick = data.get('nickname', 'Аноним').strip()

    if code not in active_rooms:
        emit('error', {'msg': 'Такой комнаты нет, чел!'})
        return
        
    room = active_rooms[code]
    if len(room['players']) >= 2:
        emit('error', {'msg': 'Комната уже забита!'})
        return
        
    join_room(code)
    room['players'].append(sid)
    room['nicks'][sid] = nick
    
    p1_sid = room['players'][0]
    p2_sid = sid
    
    emit('game_start', {
        'room': code, 'symbol': 'X', 
        'my_nick': room['nicks'][p1_sid], 'opponent': room['nicks'][p2_sid]
    }, room=p1_sid)
    
    emit('game_start', {
        'room': code, 'symbol': 'O', 
        'my_nick': room['nicks'][p2_sid], 'opponent': room['nicks'][p1_sid]
    }, room=p2_sid)

@socketio.on('player_move')
def handle_move(data):
    room_id = data.get('room')
    emit('opponent_moved', data, room=room_id, include_self=False)

@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    for code, room in list(active_rooms.items()):
        if sid in room['players']:
            emit('opponent_left', room=code, include_self=False)
            del active_rooms[code]

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    print(f"Сервер готов к замесам! Запуск на порту {port}")
    socketio.run(app, host='0.0.0.0', port=port)