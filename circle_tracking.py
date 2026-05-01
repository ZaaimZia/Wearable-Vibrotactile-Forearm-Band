import pygame
import random
import math
import csv
from dataclasses import dataclass
import threading
import queue
import asyncio

from bleak import BleakScanner, BleakClient

# BLE SETTINGS
DEVICE_NAME = "Nano33BLE"
CHAR_UUID = "19B10003-E8F2-537E-4F6C-D104768A1214"

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

def int16_to_le_bytes(x: int) -> bytes:
    x = int(clamp(x, -32768, 32767))
    return x.to_bytes(2, byteorder="little", signed=True)

class BleWorker(threading.Thread):
    def __init__(self, value_queue: "queue.Queue[int]", send_hz: float = 50.0):
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
            print(f"[BLE] {DEVICE_NAME} not found. Is the board advertising?")
            return

        async with BleakClient(target.address) as client:
            print("[BLE] Connected")

            latest = 0
            while not self.stop_event.is_set():
                try:
                    while True:
                        latest = self.q.get_nowait()
                except queue.Empty:
                    pass

                try:
                    await client.write_gatt_char(
                        CHAR_UUID,
                        int16_to_le_bytes(latest),
                        response=True
                    )
                except Exception as e:
                    print("[BLE] write failed:", e)
                    return

                await asyncio.sleep(self.send_period)

    def stop(self):
        self.stop_event.set()

@dataclass
class Params:
    width: int = 1000
    height: int = 400
    circle_radius: int = 25
    fps: int = 120
    duration_s: float = 20.0
    time_scale: float = 0.2

    tolerance_px: float = 20

    # BLE mapping
    SEND_HZ: float = 50.0
    VELOCITY_SCALE: float = 1.5
    ACCELERATION_SCALE: float = 1.0

    # Waveform display scaling
    waveform_amplitude_px: float = 300.0

    # waveform parameters
    A: float = 18.5
    w1: float = 2.031
    w2: float = 1.093

    # Random start offset range for t0
    random_start_max_s: float = 20.0

    # Intermittent disappearance timing
    visible_time_s: float = 1.7
    hidden_time_s: float = 0.3

def waveform_q(t_star: float, p: Params) -> float:
    return p.A * math.sin(p.w1 * t_star) * math.sin(p.w2 * t_star)

def waveform_dqdt(t_star: float, p: Params) -> float:
    return p.A * (
        p.w1 * math.cos(p.w1 * t_star) * math.sin(p.w2 * t_star)
        + p.w2 * math.sin(p.w1 * t_star) * math.cos(p.w2 * t_star)
    )

def waveform_d2qdt2(t_star: float, p: Params) -> float:
    return p.A * (
        - (p.w1**2 + p.w2**2) * math.sin(p.w1 * t_star) * math.sin(p.w2 * t_star)
        + 2 * p.w1 * p.w2 * math.cos(p.w1 * t_star) * math.cos(p.w2 * t_star)
    )

def run_trial(p: Params, out_csv="Alif3_AD.csv"):
    pygame.init()
    screen = pygame.display.set_mode((p.width, p.height))
    pygame.display.set_caption("Circle Tracking Task")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont(None, 28)

    y = p.height * 0.5

    t = 0.0
    running = True
    started = False

    mapping_mode = None      # "velocity", "acceleration", or "none"
    vision_mode = None       # "clear", "occluded", or "disappearance"

    # Random starting point on the waveform
    t0 = random.uniform(0.0, p.random_start_max_s)

    rows = []
    header = [
        "t_s", "x_circle", "x_cursor", "error",
        "vx", "ax", "t_star", "q_raw",
        "mapping_mode", "vision_mode"
    ]

    info_lines = [
        "Task: Follow the centre of the moving circle with your cursor.",
        "Press V to use VELOCITY mapping.",
        "Press A to use ACCELERATION mapping.",
        "Press N for NO MAPPING.",
        "Press C for CLEAR vision.",
        "Press O for OCCLUDED vision.",
        "Press D for INTERMITTENT DISAPPEARANCE.",
        "Press SPACE to start once both options are selected.",
        "Press ESC to quit."
    ]

    occluders = [
        pygame.Rect(180, 110, 90, 180),
        pygame.Rect(420, 70, 110, 260),
        pygame.Rect(700, 120, 95, 170),
    ]

    q = queue.Queue()
    ble = BleWorker(q, send_hz=p.SEND_HZ)
    ble.start()

    send_accum = 0.0
    send_period = 1.0 / p.SEND_HZ

    try:
        while running:
            dt = clock.tick(p.fps) / 1000.0
            send_accum += dt

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        running = False
                    elif event.key == pygame.K_v and not started:
                        mapping_mode = "velocity"
                    elif event.key == pygame.K_a and not started:
                        mapping_mode = "acceleration"
                    elif event.key == pygame.K_n and not started:
                        mapping_mode = "none"
                    elif event.key == pygame.K_c and not started:
                        vision_mode = "clear"
                    elif event.key == pygame.K_o and not started:
                        vision_mode = "occluded"
                    elif event.key == pygame.K_d and not started:
                        vision_mode = "disappearance"
                    elif event.key == pygame.K_SPACE and not started and mapping_mode is not None and vision_mode is not None:
                        started = True
                        t = 0.0
                        t0 = random.uniform(0.0, p.random_start_max_s)
                        rows.clear()

            screen.fill((20, 20, 24))

            if not started:
                for i, line in enumerate(info_lines):
                    surf = font.render(line, True, (220, 220, 220))
                    screen.blit(surf, (40, 60 + 35 * i))

                mapping_text = f"Selected mapping: {mapping_mode}" if mapping_mode else "Selected mapping: none"
                vision_text = f"Selected vision: {vision_mode}" if vision_mode else "Selected vision: none"

                surf1 = font.render(mapping_text, True, (255, 180, 180))
                surf2 = font.render(vision_text, True, (180, 220, 255))
                screen.blit(surf1, (40, 60 + 35 * len(info_lines)))
                screen.blit(surf2, (40, 95 + 35 * len(info_lines)))

                pygame.display.flip()
                continue

            # waveform
            t_star = p.time_scale * t + t0
            q_raw = waveform_q(t_star, p)
            dq_raw = waveform_dqdt(t_star, p)
            d2q_raw = waveform_d2qdt2(t_star, p)

            # position, velocity, acceleration in screen-space
            x = (p.width * 0.5) + (q_raw / p.A) * p.waveform_amplitude_px
            vx = (dq_raw / p.A) * p.waveform_amplitude_px * p.time_scale
            ax = (d2q_raw / p.A) * p.waveform_amplitude_px * (p.time_scale ** 2)

            left = p.circle_radius
            right = p.width - p.circle_radius
            x = clamp(x, left, right)

            mx, my = pygame.mouse.get_pos()
            error = mx - x
            rows.append([t, x, mx, error, vx, ax, t_star, q_raw, mapping_mode, vision_mode])

            # Decide whether the circle is visible in disappearance mode
            circle_visible = True
            if vision_mode == "disappearance":
                cycle_time = p.visible_time_s + p.hidden_time_s
                phase = t % cycle_time
                if phase >= p.visible_time_s:
                    circle_visible = False

            # Draw target
            if circle_visible:
                pygame.draw.circle(screen, (80, 200, 255), (int(x), int(y)), p.circle_radius)
                pygame.draw.circle(screen, (255, 0, 255), (int(x), int(y)), 5)

            pygame.draw.line(screen, (255, 255, 255), (mx, 0), (mx, p.height), 1)

            if vision_mode == "occluded":
                for rect in occluders:
                    pygame.draw.rect(screen, (55, 55, 60), rect)

            t_text = font.render(f"Time: {t:0.2f}s / {p.duration_s:.0f}s", True, (220, 220, 220))
            screen.blit(t_text, (10, 10))

            start_text = font.render(f"Random start offset t0 = {t0:.2f}s", True, (180, 180, 180))
            screen.blit(start_text, (10, 40))

            mode_text = font.render(f"Mapping: {mapping_mode}", True, (180, 255, 180))
            screen.blit(mode_text, (10, 70))

            vision_text = font.render(f"Vision: {vision_mode}", True, (180, 220, 255))
            screen.blit(vision_text, (10, 100))

            pygame.display.flip()

            if send_accum >= send_period:
                send_accum = 0.0

                if mapping_mode == "velocity":
                    signal_scaled = int(vx * p.VELOCITY_SCALE)
                elif mapping_mode == "acceleration":
                    signal_scaled = int(ax * p.ACCELERATION_SCALE)
                else:  # no mapping
                    signal_scaled = 0

                q.put(signal_scaled)

            t += dt
            if t >= p.duration_s:
                running = False

    finally:
        pygame.quit()
        ble.stop()

    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)

    if not rows:
        return None

    errors = [r[3] for r in rows]
    rmse = math.sqrt(sum(e * e for e in errors) / len(errors))
    mae = sum(abs(e) for e in errors) / len(errors)
    within = sum(1 for e in errors if abs(e) <= p.tolerance_px) / len(errors) * 100.0

    return {
        "csv": out_csv,
        "rmse_px": rmse,
        "mae_px": mae,
        "within_tol_percent": within,
        "n_samples": len(rows),
        "t0_s": t0,
        "mapping_mode": mapping_mode,
        "vision_mode": vision_mode,
    }

if __name__ == "__main__":
    params = Params()
    results = run_trial(params, out_csv="Alif3_AD.csv")
    if results:
        print("Saved:", results["csv"])
        print(f"Samples: {results['n_samples']}")
        print(f"Random start offset t0: {results['t0_s']:.2f}s")
        print(f"Mapping mode: {results['mapping_mode']}")
        print(f"Vision mode: {results['vision_mode']}")
        print(f"RMSE: {results['rmse_px']:.2f}px | MAE: {results['mae_px']:.2f}px")
        print(f"Within ±{params.tolerance_px}px: {results['within_tol_percent']:.1f}%")