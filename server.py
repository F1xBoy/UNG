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

# ИСПРАВЛЕН БАГ: теперь data=None, сервер не крашится, если фронт ничего не передал
@socketio.on('create_room')
def on_create(data=None):
    sid = request.sid
    code = generate_code()
    while code in active_rooms:
        code = generate_code()
        
    join_room(code)
    active_rooms[code] = {'players': [sid], 'host': sid}
    emit('room_created', {'code': code})

@socketio.on('join_room')
def on_join(data):
    sid = request.sid
    code = data.get('code', '').strip().upper()

    if code not in active_rooms:
        emit('error', {'msg': 'Комнаты не существует, чел!'})
        return
        
    room = active_rooms[code]
    if len(room['players']) >= 2:
        emit('error', {'msg': 'Арена уже забита!'})
        return
        
    join_room(code)
    room['players'].append(sid)
    
    host_sid = room['players'][0]
    client_sid = sid
    
    emit('game_start', {'room': code, 'role': 'host'}, room=host_sid)
    emit('game_start', {'room': code, 'role': 'client'}, room=client_sid)

# Синхронизация всего: позиция, ХП, статус атаки
@socketio.on('sync_data')
def handle_sync(data):
    room_id = data.get('room')
    emit('enemy_data', data, room=room_id, include_self=False)

@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    for code, room in list(active_rooms.items()):
        if sid in room['players']:
            emit('opponent_left', room=code, include_self=False)
            del active_rooms[code]

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    print(f"Сервер стикменов поднят на порту {port}")
    socketio.run(app, host='0.0.0.0', port=port)