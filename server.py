from gevent import monkey
monkey.patch_all()

import os
import random
import string
import logging
from flask import Flask, request
from flask_socketio import SocketIO, emit, join_room
import gevent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/')
def health():
    return 'OK'

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='gevent',
    logger=False,
    engineio_logger=False
)

# --- Firebase Setup ---
USE_FIREBASE = False
db_client = None
try:
    import firebase_admin
    from firebase_admin import credentials, firestore
    import json
    firebase_json = os.environ.get("FIREBASE_JSON")
    if firebase_json:
        cred_dict = json.loads(firebase_json)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
        db_client = firestore.client()
        USE_FIREBASE = True
        logger.info("Firebase initialized successfully via env var!")
    elif os.path.exists("firebase-key.json"):
        cred = credentials.Certificate("firebase-key.json")
        firebase_admin.initialize_app(cred)
        db_client = firestore.client()
        USE_FIREBASE = True
        logger.info("Firebase initialized from file.")
    else:
        logger.warning("Firebase credentials not found. Running without DB.")
except Exception as e:
    logger.error(f"Firebase Init Error: {e}")

def get_player_coins(username):
    if not USE_FIREBASE or not username: return 0
    try:
        doc = db_client.collection('players').document(username).get()
        if doc.exists:
            return doc.to_dict().get('coins', 0)
    except Exception as e:
        logger.error(f"Firebase read error: {e}")
    return 0

def update_player_coins(username, coins):
    if not USE_FIREBASE or not username: return
    try:
        db_client.collection('players').document(username).set({'coins': coins}, merge=True)
    except Exception as e:
        logger.error(f"Firebase write error: {e}")

# --- Constants ---
GRAVITY = 0.7
FLOOR = 550
JUMP_FORCE = -15
SPEED = 7.5
ZOMBIE_SPEED = 2.0
TICK_RATE = 0.05  # 20 FPS

platforms = [
    {'x': 150, 'y': 400, 'w': 200, 'h': 20},
    {'x': 650, 'y': 400, 'w': 200, 'h': 20},
    {'x': 400, 'y': 250, 'w': 200, 'h': 20}
]

active_rooms = {}
connected_players = {}

class Player:
    def __init__(self, sid, username, x, color, isFacingRight):
        self.sid = sid
        self.username = username
        self.x = x
        self.y = 100
        self.vx = 0
        self.vy = 0
        self.hp = 100
        self.color = color
        self.isFacingRight = isFacingRight
        self.isAttacking = False
        self.attackTimer = 0
        self.keys = {'left': False, 'right': False, 'up': False}
        self.coins = 0

class Zombie:
    def __init__(self, id, x, y):
        self.id = id
        self.x = x
        self.y = y
        self.vx = 0
        self.vy = 0
        self.hp = 50
        self.isFacingRight = True
        self.attackTimer = 0

class Room:
    def __init__(self, code, mode):
        self.code = code
        self.mode = mode
        self.players = {}
        self.zombies = {}
        self.wave = 0
        self.zombies_to_spawn = 0
        self.zombie_counter = 0
        self.game_active = False

def generate_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))

# --- Game Loop ---
def game_loop():
    logger.info("Game loop started")
    while True:
        if not any(room.game_active for room in active_rooms.values()):
            gevent.sleep(0.5)
            continue

        for code, room in list(active_rooms.items()):
            if not room.game_active:
                continue

            # Update Players
            for sid, p in room.players.items():
                if p.hp <= 0:
                    continue
                if p.attackTimer > 0:
                    p.attackTimer -= 1

                if p.keys['left']:
                    p.vx = -SPEED
                    p.isFacingRight = False
                elif p.keys['right']:
                    p.vx = SPEED
                    p.isFacingRight = True
                else:
                    p.vx = 0

                on_ground = False
                if p.y >= FLOOR:
                    on_ground = True
                for plat in platforms:
                    if plat['x'] - 15 < p.x < plat['x'] + plat['w'] + 15 and p.y == plat['y']:
                        on_ground = True

                if p.keys['up'] and on_ground:
                    p.vy = JUMP_FORCE

                p.vy += GRAVITY
                p.x += p.vx
                p.y += p.vy

                if p.vy > 0:
                    for plat in platforms:
                        if (plat['x'] - 15 < p.x < plat['x'] + plat['w'] + 15
                                and p.y - p.vy <= plat['y']
                                and p.y >= plat['y']):
                            p.y = plat['y']
                            p.vy = 0

                p.x = max(30, min(970, p.x))
                if p.y > FLOOR:
                    p.y = FLOOR
                    p.vy = 0

            # Update PvE (Zombies)
            if room.mode == "PvE":
                if len(room.zombies) == 0 and room.zombies_to_spawn == 0:
                    room.wave += 1
                    room.zombies_to_spawn = room.wave * 3

                if room.zombies_to_spawn > 0 and random.random() < 0.03:
                    zid = f"z_{room.zombie_counter}"
                    room.zombie_counter += 1
                    spawn_x = random.choice([50, 950])
                    room.zombies[zid] = Zombie(zid, spawn_x, 100)
                    room.zombies_to_spawn -= 1

                for zid, z in list(room.zombies.items()):
                    if z.attackTimer > 0:
                        z.attackTimer -= 1

                    nearest_p = None
                    min_dist = 9999
                    for sid, p in room.players.items():
                        if p.hp > 0:
                            dist = abs(p.x - z.x) + abs(p.y - z.y)
                            if dist < min_dist:
                                min_dist = dist
                                nearest_p = p

                    if nearest_p:
                        if z.x < nearest_p.x - 40:
                            z.vx = ZOMBIE_SPEED
                            z.isFacingRight = True
                        elif z.x > nearest_p.x + 40:
                            z.vx = -ZOMBIE_SPEED
                            z.isFacingRight = False
                        else:
                            z.vx = 0
                            if z.attackTimer == 0 and abs(z.y - nearest_p.y) < 80:
                                nearest_p.hp -= 15
                                z.attackTimer = 40
                                if nearest_p.hp < 0:
                                    nearest_p.hp = 0
                    else:
                        z.vx = 0

                    z.vy += GRAVITY
                    z.x += z.vx
                    z.y += z.vy

                    if z.vy > 0:
                        for plat in platforms:
                            if (plat['x'] - 15 < z.x < plat['x'] + plat['w'] + 15
                                    and z.y - z.vy <= plat['y']
                                    and z.y >= plat['y']):
                                z.y = plat['y']
                                z.vy = 0

                    z.x = max(30, min(970, z.x))
                    if z.y > FLOOR:
                        z.y = FLOOR
                        z.vy = 0

            # Broadcast State
            state = {
                'players': {
                    sid: {
                        'x': p.x, 'y': p.y, 'hp': p.hp,
                        'isFacingRight': p.isFacingRight,
                        'isAttacking': p.attackTimer > 0,
                        'username': p.username, 'color': p.color,
                        'coins': p.coins
                    } for sid, p in room.players.items()
                },
                'zombies': {
                    zid: {
                        'x': z.x, 'y': z.y, 'hp': z.hp,
                        'isFacingRight': z.isFacingRight,
                        'isAttacking': z.attackTimer > 0
                    } for zid, z in room.zombies.items()
                },
                'wave': room.wave
            }
            socketio.emit('game_state', state, room=code)

            # Важно: дать другим задачам выполниться
            gevent.sleep(0)

        gevent.sleep(TICK_RATE)

socketio.start_background_task(game_loop)

# --- SocketIO Events ---
@socketio.on('create_room')
def on_create(data):
    sid = request.sid
    logger.info(f"CREATE ROOM: sid={sid}, data={data}")
    username = str(data.get('username', 'Player')).strip()
    if not username:
        username = "Player"
    mode = data.get('mode', 'PvE')

    code = generate_code()
    while code in active_rooms:
        code = generate_code()

    join_room(code)
    room = Room(code, mode)
    coins = get_player_coins(username)

    p = Player(sid, username, 250, 'var(--primary)', True)
    p.coins = coins
    room.players[sid] = p
    active_rooms[code] = room
    connected_players[sid] = {'username': username, 'room': code}

    emit('room_created', {'code': code})
    logger.info(f"ROOM CREATED: {code}, mode={mode}")

    if mode == "PvE":
        room.game_active = True
        emit('game_start', {'room': code, 'mode': mode, 'my_id': sid})
        logger.info(f"GAME START (PvE) sent to {sid}")

@socketio.on('join_room')
def on_join(data):
    sid = request.sid
    code = str(data.get('code', '')).strip().upper()
    username = str(data.get('username', 'Player')).strip()
    if not username:
        username = "Player"

    if code not in active_rooms:
        emit('error', {'msg': 'Комнаты не существует!'})
        return

    room = active_rooms[code]
    max_players = 2 if room.mode == "PvP" else 4
    if len(room.players) >= max_players:
        emit('error', {'msg': 'Арена заполнена!'})
        return

    join_room(code)
    coins = get_player_coins(username)
    colors = ['var(--primary)', 'var(--secondary)', '#ff0266', '#03dac6']
    color = colors[len(room.players) % 4]

    p = Player(sid, username, 750, color, False)
    p.coins = coins
    room.players[sid] = p
    connected_players[sid] = {'username': username, 'room': code}

    if room.mode == "PvP" and len(room.players) == 2:
        room.game_active = True
        emit('game_start', {'room': code, 'mode': room.mode, 'my_id': sid}, room=code)
    elif room.mode == "PvE":
        emit('game_start', {'room': code, 'mode': room.mode, 'my_id': sid})

@socketio.on('player_input')
def handle_input(data):
    sid = request.sid
    if sid in connected_players:
        code = connected_players[sid]['room']
        if code in active_rooms:
            room = active_rooms[code]
            if sid in room.players:
                p = room.players[sid]
                action = data.get('action')
                state = data.get('state', False)
                if action in ['left', 'right', 'up']:
                    p.keys[action] = state

@socketio.on('attack')
def handle_attack():
    sid = request.sid
    if sid in connected_players:
        code = connected_players[sid]['room']
        if code in active_rooms:
            room = active_rooms[code]
            if sid in room.players:
                p = room.players[sid]
                if p.hp <= 0 or p.attackTimer > 0:
                    return
                p.attackTimer = 15

                reach = p.x + 95 if p.isFacingRight else p.x - 95

                if room.mode == "PvE":
                    for zid, z in list(room.zombies.items()):
                        hit = False
                        if p.isFacingRight and z.x > p.x and z.x - 20 < reach and abs(p.y - z.y) < 80:
                            hit = True
                        if not p.isFacingRight and z.x < p.x and z.x + 20 > reach and abs(p.y - z.y) < 80:
                            hit = True
                        if hit:
                            z.hp -= 25
                            if z.hp <= 0:
                                del room.zombies[zid]
                                p.coins += 10
                                update_player_coins(p.username, p.coins)

                elif room.mode == "PvP":
                    for osid, op in room.players.items():
                        if osid != sid and op.hp > 0:
                            hit = False
                            if p.isFacingRight and op.x > p.x and op.x - 20 < reach and abs(p.y - op.y) < 80:
                                hit = True
                            if not p.isFacingRight and op.x < p.x and op.x + 20 > reach and abs(p.y - op.y) < 80:
                                hit = True
                            if hit:
                                op.hp -= 15
                                if op.hp < 0:
                                    op.hp = 0

@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    logger.info(f"DISCONNECT: {sid}")
    if sid in connected_players:
        code = connected_players[sid]['room']
        if code in active_rooms:
            room = active_rooms[code]
            if sid in room.players:
                del room.players[sid]
            if len(room.players) == 0:
                del active_rooms[code]
            else:
                socketio.emit('opponent_left', room=code)
        del connected_players[sid]

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Starting server on port {port}")
    socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)