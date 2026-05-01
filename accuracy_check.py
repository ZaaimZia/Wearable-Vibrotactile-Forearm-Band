import pygame
import random
import math
import csv
import asyncio
import threading
import queue
import time
from dataclasses import dataclass

from bleak import BleakScanner, BleakClient

DEVICE_NAME = "Nano33BLE"
CHAR_UUID = "19B10004-E8F2-537E-4F6C-D104768A1214"

NUM_MOTORS = 8

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

def angle_wrap_deg(a):
    return a % 360

def angular_error_deg(a, b):
    return (a - b + 180) % 360 - 180

def motor_angle_deg(i):
    # Motor 0 = top-right, Motor 7 = top-left.
    # Numbers increase clockwise around the band.
    return 67.5 - (i * 45)

class BleWorker(threading.Thread):
    def __init__(self, value_queue: "queue.Queue[bytes]", send_hz=20):
        super().__init__(daemon=True)
        self.q = value_queue
        self.send_period = 1.0 / send_hz
        self.stop_event = threading.Event()

    def run(self):
        asyncio.run(self._main())

    async def _main(self):
        devices = await BleakScanner.discover(timeout=5.0)
        target = next((d for d in devices if d.name == DEVICE_NAME), None)

        if not target:
            print("[BLE] Nano33BLE not found")
            return

        async with BleakClient(target.address) as client:
            print("[BLE] Connected")

            latest = bytes([0] * NUM_MOTORS)

            while not self.stop_event.is_set():
                try:
                    while True:
                        latest = self.q.get_nowait()
                except queue.Empty:
                    pass

                try:
                    await client.write_gatt_char(CHAR_UUID, latest, response=True)
                except Exception as e:
                    print("[BLE] write failed:", e)
                    return

                await asyncio.sleep(self.send_period)

    def stop(self):
        self.stop_event.set()

@dataclass
class Params:
    width: int = 800
    height: int = 600
    band_radius: int = 190
    knob_radius: int = 45
    motor_dot_radius: int = 10
    max_intensity: int = 255
    min_intensity: int = 40
    send_hz: float = 20.0

def motor_amplitudes_for_angle(angle_deg, p: Params):
    angle = angle_wrap_deg(angle_deg)

    motor_angles = [angle_wrap_deg(motor_angle_deg(i)) for i in range(NUM_MOTORS)]

    distances = [abs(angular_error_deg(angle, ma)) for ma in motor_angles]
    sorted_indices = sorted(range(NUM_MOTORS), key=lambda i: distances[i])

    m1 = sorted_indices[0]
    m2 = sorted_indices[1]

    d1 = distances[m1]
    d2 = distances[m2]

    total = d1 + d2
    if total == 0:
        w1 = 1.0
        w2 = 0.0
    else:
        w1 = d2 / total
        w2 = d1 / total

    values = [0] * NUM_MOTORS
    values[m1] = int(p.min_intensity + w1 * (p.max_intensity - p.min_intensity))
    values[m2] = int(p.min_intensity + w2 * (p.max_intensity - p.min_intensity))

    return bytes(values), m1, m2

def angle_to_xy(cx, cy, radius, angle_deg):
    rad = math.radians(angle_deg)
    x = cx + radius * math.cos(rad)
    y = cy - radius * math.sin(rad)
    return int(x), int(y)

def mouse_angle_deg(cx, cy, mx, my):
    dx = mx - cx
    dy = cy - my
    return angle_wrap_deg(math.degrees(math.atan2(dy, dx)))

def run_experiment(p: Params, out_csv="angle_accuracy_data.csv"):
    pygame.init()
    screen = pygame.display.set_mode((p.width, p.height))
    pygame.display.set_caption("Wristband Angle Accuracy Test")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont(None, 28)

    cx = p.width // 2
    cy = p.height // 2

    q = queue.Queue()
    ble = BleWorker(q, send_hz=p.send_hz)
    ble.start()

    target_angle = random.uniform(0, 360)
    knob_angle = 0.0
    trial = 1

    rows = []
    submitted_error = None

    running = True

    try:
        while running:
            clock.tick(60)

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False

                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        running = False

                    elif event.key == pygame.K_SPACE:
                        target_angle = random.uniform(0, 360)
                        submitted_error = None
                        trial += 1

                    elif event.key == pygame.K_RETURN:
                        err = angular_error_deg(knob_angle, target_angle)
                        submitted_error = err
                        rows.append([
                            trial,
                            time.time(),
                            target_angle,
                            knob_angle,
                            err,
                            abs(err)
                        ])
                        print(
                            f"Trial {trial}: "
                            f"target={target_angle:.1f}, "
                            f"response={knob_angle:.1f}, "
                            f"error={err:.1f}"
                        )

            mx, my = pygame.mouse.get_pos()
            knob_angle = mouse_angle_deg(cx, cy, mx, my)

            amplitudes, lower_motor, upper_motor = motor_amplitudes_for_angle(target_angle, p)
            print("Target:", round(target_angle, 1), "Amplitudes:", list(amplitudes))
            q.put(amplitudes)

            screen.fill((20, 20, 24))

            # Wristband circle
            pygame.draw.circle(screen, (180, 180, 180), (cx, cy), p.band_radius, 3)

            # Motor positions - all look identical so active motors are hidden
            for i in range(NUM_MOTORS):
                motor_angle = motor_angle_deg(i)
                x, y = angle_to_xy(cx, cy, p.band_radius, motor_angle)

                pygame.draw.circle(screen, (120, 120, 130), (x, y), p.motor_dot_radius)

                label = font.render(str(i), True, (230, 230, 230))
                screen.blit(label, (x + 12, y - 12))

            # Centre knob
            pygame.draw.circle(screen, (90, 90, 100), (cx, cy), p.knob_radius)
            pygame.draw.circle(screen, (220, 220, 220), (cx, cy), p.knob_radius, 2)

            # Knob direction line
            lx, ly = angle_to_xy(cx, cy, p.band_radius - 20, knob_angle)
            pygame.draw.line(screen, (80, 200, 255), (cx, cy), (lx, ly), 5)

            title = font.render(
                "Move mouse around knob to choose angle. ENTER = submit. SPACE = new target.",
                True,
                (230, 230, 230)
            )
            screen.blit(title, (30, 25))

            trial_text = font.render(f"Trial: {trial}", True, (230, 230, 230))
            screen.blit(trial_text, (30, 60))

            angle_text = font.render(f"Knob angle: {knob_angle:.1f}°", True, (180, 220, 255))
            screen.blit(angle_text, (30, 90))

            instruction_text = font.render(
                "Use vibration to locate the hidden target angle.",
                True,
                (200, 200, 200)
            )
            screen.blit(instruction_text, (30, 120))

            if submitted_error is not None:
                err_text = font.render(
                    f"Last error: {submitted_error:.1f}° | abs: {abs(submitted_error):.1f}°",
                    True,
                    (255, 220, 120)
                )
                screen.blit(err_text, (30, 150))

            pygame.display.flip()

    finally:
        # Turn motors off before leaving
        q.put(bytes([0] * NUM_MOTORS))

        pygame.quit()
        ble.stop()

        with open(out_csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                "trial",
                "timestamp",
                "target_angle_deg",
                "response_angle_deg",
                "signed_error_deg",
                "absolute_error_deg"
            ])
            w.writerows(rows)

        print("Saved:", out_csv)

if __name__ == "__main__":
    params = Params()
    run_experiment(params)