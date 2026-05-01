#include <Wire.h>
#include <ArduinoBLE.h>
#include <Adafruit_PWMServoDriver.h>

Adafruit_PWMServoDriver pwm(0x7F);

const int NUM_MOTORS = 8;
const int FIRST_CH = 0;
const int PWM_FREQ = 1000;

BLEService hapticService("19B10000-E8F2-537E-4F6C-D104768A1214");

BLECharacteristic motorChar(
  "19B10004-E8F2-537E-4F6C-D104768A1214",
  BLEWrite | BLEWriteWithoutResponse,
  NUM_MOTORS
);

void setMotor(int motorIndex, uint8_t intensity) {
  float level = intensity / 255.0f;
  uint16_t duty = (uint16_t)(level * 4095.0f);
  pwm.setPWM(FIRST_CH + motorIndex, 0, duty);
}

void allOff() {
  for (int i = 0; i < NUM_MOTORS; i++) {
    setMotor(i, 0);
  }
}

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
  BLE.setAdvertisedService(hapticService);

  hapticService.addCharacteristic(motorChar);
  BLE.addService(hapticService);

  BLE.advertise();
  Serial.println("Advertising Nano33BLE...");
}

void loop() {
  BLEDevice central = BLE.central();

  if (!central) {
    allOff();
    return;
  }

  Serial.println("Connected");

  while (central.connected()) {
    if (motorChar.written()) {
      uint8_t values[NUM_MOTORS];
      motorChar.readValue(values, NUM_MOTORS);

      for (int i = 0; i < NUM_MOTORS; i++) {
        setMotor(i, values[i]);
      }
    }
  }

  allOff();
  Serial.println("Disconnected");
}