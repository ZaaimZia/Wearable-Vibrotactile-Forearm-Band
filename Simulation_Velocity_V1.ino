#include <Wire.h>
#include <ArduinoBLE.h>
#include <Adafruit_PWMServoDriver.h>

// -------------------- PCA9685 --------------------
Adafruit_PWMServoDriver pwm(0x7F);

const int NUM_MOTORS = 8;
const int FIRST_CH = 0;
const int PWM_FREQ = 2000;

// -------------------- BLE --------------------
BLEService controlService("19B10000-E8F2-537E-4F6C-D104768A1214");

BLECharacteristic vxChar(
  "19B10003-E8F2-537E-4F6C-D104768A1214",
  BLEWrite | BLEWriteWithoutResponse,
  2
);

// -------------------- USER PARAMETERS --------------------
// how long each motor stays on
const unsigned long motorOnTimeMs = 150;

// scaling from velocity magnitude to time between motors
// larger value = slower wave overall
const float stepTimeScale = 60000.0f;

//  minimum velocity before anything happens
const int deadband = 0;

// Fixed intensity
const float motorLevel = 1.00f;

// -------------------- STATE --------------------
int16_t vx_scaled = 0;
int currentMotor = 0;
int direction = 1;

unsigned long lastStepTime = 0;

bool motorActive[NUM_MOTORS] = {false};
unsigned long motorOffTime[NUM_MOTORS] = {0};

// -------------------- HELPERS --------------------
void setMotorLevel(int ch, float level) {
  if (level < 0.0f) level = 0.0f;
  if (level > 1.0f) level = 1.0f;

  uint16_t duty = (uint16_t)(level * 4095.0f);
  pwm.setPWM(ch, 0, duty);
}

void turnMotorOn(int idx) {
  setMotorLevel(FIRST_CH + idx, motorLevel);
  motorActive[idx] = true;
  motorOffTime[idx] = millis() + motorOnTimeMs;
}

void turnMotorOff(int idx) {
  setMotorLevel(FIRST_CH + idx, 0.0f);
  motorActive[idx] = false;
}

void allOff() {
  for (int i = 0; i < NUM_MOTORS; i++) {
    turnMotorOff(i);
  }
}

unsigned long computeStepIntervalMs(int16_t v) {
  int mag = abs(v);

  if (mag <= deadband) {
    return 0;
  }

  // interval = scale / |velocity|
  float interval = stepTimeScale / (float)mag;

  // avoid silly values
  if (interval < 10.0f) interval = 10.0f;
  if (interval > 1000.0f) interval = 1000.0f;

  return (unsigned long)interval;
}

// -------------------- SETUP --------------------
void setup() {
  Serial.begin(115200);

  Wire.begin();
  pwm.begin();
  pwm.setPWMFreq(PWM_FREQ);
  allOff();

  if (!BLE.begin()) {
    while (1) {}
  }

  BLE.setLocalName("Nano33BLE");
  BLE.setAdvertisedService(controlService);
  controlService.addCharacteristic(vxChar);
  BLE.addService(controlService);
  BLE.advertise();

  Serial.println("Advertising Nano33BLE...");
}

// -------------------- LOOP --------------------
void loop() {
  BLEDevice central = BLE.central();

  if (!central) {
    allOff();
    return;
  }

  Serial.print("Connected: ");
  Serial.println(central.address());

  while (central.connected()) {
    unsigned long now = millis();

    // Read new BLE velocity if available
    if (vxChar.written()) {
      uint8_t b[2];
      vxChar.readValue(b, 2);
      vx_scaled = (int16_t)((b[1] << 8) | b[0]);

      if (vx_scaled > 0) {
        direction = 1;
      } else if (vx_scaled < 0) {
        direction = -1;
      }

      Serial.print("vx_scaled = ");
      Serial.println(vx_scaled);
    }

    // Turn off motors whose on-time has expired
    for (int i = 0; i < NUM_MOTORS; i++) {
      if (motorActive[i] && ((long)(now - motorOffTime[i]) >= 0)) {
        turnMotorOff(i);
      }
    }

    // If velocity is too small, keep everything off
    unsigned long stepIntervalMs = computeStepIntervalMs(vx_scaled);

    if (stepIntervalMs == 0) {
      allOff();
      continue;
    }

    // Time to move to next motor in the sequence
    if (now - lastStepTime >= stepIntervalMs) {
      lastStepTime = now;

      currentMotor += direction;

      if (currentMotor >= NUM_MOTORS) currentMotor = 0;
      if (currentMotor < 0) currentMotor = NUM_MOTORS - 1;

      turnMotorOn(currentMotor);
    }
  }

  Serial.println("Disconnected");
  allOff();
}