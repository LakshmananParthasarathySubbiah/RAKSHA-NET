#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <SPI.h>
#include <SD.h>
#include <BLEDevice.h>
#include <BLEUtils.h>
#include <BLEScan.h>
#include <string.h>

// YD-ESP32-S3 I2C pins from the board pinout.
constexpr uint8_t LCD_SDA_PIN = 8;
constexpr uint8_t LCD_SCL_PIN = 9;
constexpr uint8_t MPU6500_ADDR = 0x68;

// MicroSD module SPI pins.
constexpr uint8_t SD_CS_PIN = 10;
constexpr uint8_t SD_MOSI_PIN = 11;
constexpr uint8_t SD_MISO_PIN = 12;
constexpr uint8_t SD_SCK_PIN = 13;
constexpr char FALL_LOG_FILE[] = "/fall_log.csv";
constexpr char AI_FALL_LOG_FILE[] = "/ai_fall_log.csv";
constexpr char GSM_LOG_FILE[] = "/gsm_log.csv";

// Alert hardware pins. Button uses INPUT_PULLUP: wire button between GPIO4 and GND.
constexpr uint8_t ALERT_BUTTON_PIN = 14;
constexpr uint8_t BUZZER_PIN = 21;
constexpr uint32_t FALL_CONFIRM_WINDOW_MS = 12000;
constexpr uint32_t BUZZER_BEEP_MS = 300;

// SIM800L EVB UART pins. GSM TX -> GPIO18/RX1, GSM RX -> GPIO17/TX1.
constexpr uint8_t GSM_RX_PIN = 18;
constexpr uint8_t GSM_TX_PIN = 17;
const uint32_t GSM_BAUD_RATES[] = {9600, 115200, 57600, 38400, 19200, 4800};
uint32_t gsmBaud = 9600;
const char PHONE_NUMBER_1[] = "PHONE_NUMBER_1_HERE";
const char PHONE_NUMBER_2[] = "PHONE_NUMBER_2_HERE";

HardwareSerial gsmSerial(1);

// BLE room beacon scan settings.
constexpr uint8_t BEACON_SCAN_SECONDS = 3;
constexpr int BEACON_MIN_RSSI = -95;
constexpr char BEACON_PREFIX[] = "ROOM_";
constexpr uint16_t LCD_PAGE_DELAY_MS = 1000;
const char *ROOM_BEACON_NAMES[] = {
  "ROOM_Bedroom",
  "ROOM_Hallroom",
  "ROOM_Washroom"
};

// 16x2 I2C LCD address used in the car blackbox reference code.
LiquidCrystal_I2C lcd(0x27, 16, 2);

float accelX = 0.0;
float accelY = 0.0;
float accelZ = 0.0;
float gyroX = 0.0;
float gyroY = 0.0;
float gyroZ = 0.0;
uint8_t lcdPage = 0;
bool sdReady = false;
bool gsmReady = false;
char predictionStatus[9] = "WAIT";
float predictionConfidence = 0.0;
float fallAlertConfidence = 0.0;  // confidence of the FALL that started the alert
uint32_t predictionDisplayUntil = 0;
bool fallAlertActive = false;
bool fallAlertSmsSent = false;
bool lastPredictionWasFall = false;
bool buzzerOn = false;
uint32_t fallAlertStartedAt = 0;
uint32_t lastBuzzerToggleAt = 0;
char currentRoom[17] = "Outside_Range";
int currentRoomRssi = 0;
char nearestBeaconName[17] = "";
int nearestBeaconRssi = -999;
portMUX_TYPE roomMux = portMUX_INITIALIZER_UNLOCKED;

bool isKnownRoomBeacon(const String &deviceName);

class RoomBeaconCallbacks : public BLEAdvertisedDeviceCallbacks {
  void onResult(BLEAdvertisedDevice advertisedDevice) {
    if (!advertisedDevice.haveName()) {
      return;
    }

    String deviceName = advertisedDevice.getName().c_str();
    if (!isKnownRoomBeacon(deviceName)) {
      return;
    }

    int rssi = advertisedDevice.getRSSI();
    if (rssi > nearestBeaconRssi) {
      nearestBeaconRssi = rssi;
      deviceName.remove(0, strlen(BEACON_PREFIX));
      deviceName.toCharArray(nearestBeaconName, sizeof(nearestBeaconName));
    }
  }
};

bool isKnownRoomBeacon(const String &deviceName) {
  for (uint8_t i = 0; i < sizeof(ROOM_BEACON_NAMES) / sizeof(ROOM_BEACON_NAMES[0]); i++) {
    if (deviceName == ROOM_BEACON_NAMES[i]) {
      return true;
    }
  }

  return false;
}

void setup() {
  Serial.begin(115200);
  pinMode(ALERT_BUTTON_PIN, INPUT_PULLUP);
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, LOW);
  delay(1000);
  logLine("");
  logLine("RAKSHA-NET starting...");

  Wire.begin(LCD_SDA_PIN, LCD_SCL_PIN);

  lcd.begin(16,2);
  lcd.backlight();
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("RAKSHA-NET");
  lcd.setCursor(0, 1);
  lcd.print("SD Card Init");

  sdReady = initSDCard();
  if (sdReady) {
    // SD hardware test passed. Keep sample logging disabled for normal runs.
    // writeSampleFallLog();
    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print("SD Card OK");
    lcd.setCursor(0, 1);
    lcd.print("fall_log.csv");
    delay(1500);
  } else {
    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print("SD Card Error");
    lcd.setCursor(0, 1);
    lcd.print("Check wiring");
    delay(1500);
  }

  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("GSM Init");
  lcd.setCursor(0, 1);
  lcd.print("SIM800L");

  gsmReady = initGSM();
  if (gsmReady) {
    // GSM hardware test passed. SMS is sent only after a confirmed fall.
    // sendAlertSMSToAll("RAKSHA-NET GSM test message");

    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print("GSM Ready");
    lcd.setCursor(0, 1);
    lcd.print("Alert SMS Ready");
    delay(2000);
  } else {
    saveGSMLog("GSM_INIT_FAILED", "No AT response from SIM800L");
    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print("GSM Error");
    lcd.setCursor(0, 1);
    lcd.print("Check SIM/PWR");
    delay(2000);
  }

  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("RAKSHA-NET");
  lcd.setCursor(0, 1);
  lcd.print("MPU6500 Init");

  if (!initMPU6500()) {
    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print("MPU6500 Error");
    lcd.setCursor(0, 1);
    lcd.print("Check wiring");
    logLine("MPU6500 not found.");
    logLine("Check SDA=GPIO8, SCL=GPIO9, VCC=3.3V, GND.");
    logLine("If wiring is correct, try changing MPU6500_ADDR from 0x68 to 0x69.");
    while (true) {
      delay(1000);
    }
  }

  logLine("MPU6500 ready.");
  startBeaconScanner();
  delay(1000);
  lcd.clear();
}

void loop() {
  handlePredictionCommand();
  handleFallAlert();

  if (!readMPU6500()) {
    logLine("MPU6500 read failed.");
    delay(500);
    handleFallAlert();
    return;
  }

  logSensorData();

  char roomCopy[17];
  int rssiCopy = 0;
  getCurrentRoom(roomCopy, sizeof(roomCopy), &rssiCopy);

  lcd.clear();
  if (fallAlertActive) {
    showFallCountdownOnLCD();
  } else if (millis() < predictionDisplayUntil) {
    showPredictionOnLCD();
  } else if (lcdPage == 0) {
    lcd.setCursor(0, 0);
    if (strcmp(roomCopy, "Outside_Range") == 0) {
      lcd.print("Outside_Range");
      lcd.setCursor(0, 1);
      lcd.print("No beacon");
    } else {
      lcd.print("Room:");
      lcd.print(roomCopy);
      lcd.setCursor(0, 1);
      lcd.print("RSSI:");
      lcd.print(rssiCopy);
    }
  } else if (lcdPage == 1) {
    lcd.setCursor(0, 0);
    lcd.print("X:");
    lcd.print(accelX, 2);
    lcd.print(" Y:");
    lcd.print(accelY, 2);

    lcd.setCursor(0, 1);
    lcd.print("Z:");
    lcd.print(accelZ, 2);
    lcd.print(" g");
  } else {
    lcd.setCursor(0, 0);
    lcd.print("GX:");
    lcd.print(gyroX, 1);
    lcd.print(" GY:");
    lcd.print(gyroY, 1);

    lcd.setCursor(0, 1);
    lcd.print("GZ:");
    lcd.print(gyroZ, 1);
    lcd.print(" dps");
  }

  lcdPage = (lcdPage + 1) % 3;
  waitWithAlertHandling(LCD_PAGE_DELAY_MS);
}

void waitWithAlertHandling(uint32_t waitMs) {
  uint32_t startTime = millis();
  while (millis() - startTime < waitMs) {
    handlePredictionCommand();
    handleFallAlert();
    delay(25);
  }
}

void handlePredictionCommand() {
  if (!Serial.available()) {
    return;
  }

  String command = Serial.readStringUntil('\n');
  command.trim();

  if (!command.startsWith("PRED,")) {
    return;
  }

  int firstComma = command.indexOf(',');
  int secondComma = command.indexOf(',', firstComma + 1);
  if (secondComma < 0) {
    return;
  }

  String status = command.substring(firstComma + 1, secondComma);
  status.toUpperCase();
  status.toCharArray(predictionStatus, sizeof(predictionStatus));
  predictionConfidence = command.substring(secondComma + 1).toFloat();

  if (status == "FALL") {
    predictionDisplayUntil = millis() + 5000;
    if (!lastPredictionWasFall) {
      startFallAlert();
    }
    lastPredictionWasFall = true;
  } else {
    lastPredictionWasFall = false;
    if (status == "CHECK") {
      predictionDisplayUntil = millis() + 2000;
    } else {
      predictionDisplayUntil = 0;
    }
  }
}

void showPredictionOnLCD() {
  lcd.setCursor(0, 0);
  if (strcmp(predictionStatus, "FALL") == 0) {
    lcd.print("!! FALL ALERT !!");
  } else if (strcmp(predictionStatus, "NORMAL") == 0) {
    lcd.print("Status: NORMAL ");
  } else {
    lcd.print("Status: CHECK  ");
  }

  lcd.setCursor(0, 1);
  lcd.print("Conf:");
  lcd.print(predictionConfidence, 2);
  lcd.print(" AI");
}

void startFallAlert() {
  if (fallAlertActive) {
    return;
  }

  uint32_t now = millis();
  fallAlertActive = true;
  fallAlertSmsSent = false;
  buzzerOn = false;
  fallAlertStartedAt = now;
  lastBuzzerToggleAt = now;
  predictionDisplayUntil = now + FALL_CONFIRM_WINDOW_MS;
  digitalWrite(BUZZER_PIN, LOW);
  fallAlertConfidence = predictionConfidence;
  logFallEvent("FALL_DETECTED_WAITING");
}

void handleFallAlert() {
  if (!fallAlertActive) {
    return;
  }

  uint32_t now = millis();
  if (digitalRead(ALERT_BUTTON_PIN) == LOW) {
    cancelFallAlert();
    return;
  }

  if (now - lastBuzzerToggleAt >= BUZZER_BEEP_MS) {
    buzzerOn = !buzzerOn;
    digitalWrite(BUZZER_PIN, buzzerOn ? HIGH : LOW);
    lastBuzzerToggleAt = now;
  }

  if (now - fallAlertStartedAt >= FALL_CONFIRM_WINDOW_MS) {
    sendConfirmedFallAlert();
  }
}

void cancelFallAlert() {
  fallAlertActive = false;
  fallAlertSmsSent = false;
  buzzerOn = false;
  digitalWrite(BUZZER_PIN, LOW);
  strncpy(predictionStatus, "NORMAL", sizeof(predictionStatus) - 1);
  predictionStatus[sizeof(predictionStatus) - 1] = '\0';
  predictionDisplayUntil = millis() + 5000;
  logFallEvent("FALL_CANCELLED_BUTTON");
}

void sendConfirmedFallAlert() {
  if (fallAlertSmsSent) {
    return;
  }

  fallAlertSmsSent = true;
  fallAlertActive = false;
  buzzerOn = false;
  digitalWrite(BUZZER_PIN, LOW);

  char roomCopy[17];
  int rssiCopy = 0;
  getCurrentRoom(roomCopy, sizeof(roomCopy), &rssiCopy);

  String message = "RAKSHA-NET FALL ALERT. Room: " + String(roomCopy) +
                   ", RSSI: " + String(rssiCopy) +
                   ", AI confidence: " + String(fallAlertConfidence, 2);

  if (gsmReady) {
    showSendingSmsOnLCD(0);
    sendAlertSMSToAll(message.c_str());
  } else {
    saveGSMLog("FALL_SMS_NOT_SENT", "GSM not ready");
  }

  logFallEvent("FALL_CONFIRMED_SMS_SENT");
  strncpy(predictionStatus, "FALL", sizeof(predictionStatus) - 1);
  predictionStatus[sizeof(predictionStatus) - 1] = '\0';
  predictionDisplayUntil = millis() + 10000;
}

void showFallCountdownOnLCD() {
  uint32_t elapsed = millis() - fallAlertStartedAt;
  uint32_t remaining = 0;
  if (elapsed < FALL_CONFIRM_WINDOW_MS) {
    remaining = (FALL_CONFIRM_WINDOW_MS - elapsed + 999) / 1000;
  }

  lcd.setCursor(0, 0);
  lcd.print("FALL? Btn=Cancel");
  lcd.setCursor(0, 1);
  lcd.print("SMS in ");
  lcd.print(remaining);
  lcd.print(" sec   ");
}

void showSendingSmsOnLCD(uint8_t smsNumber) {
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("Fall confirmed");
  lcd.setCursor(0, 1);
  if (smsNumber == 0) {
    lcd.print("Starting SMS...");
  } else {
    lcd.print("Sending SMS ");
    lcd.print(smsNumber);
    lcd.print("   ");
  }
}

bool initMPU6500() {
  Wire.beginTransmission(MPU6500_ADDR);
  if (Wire.endTransmission() != 0) {
    return false;
  }

  writeMPU6500Register(0x6B, 0x00);  // Wake sensor from sleep.
  delay(100);
  writeMPU6500Register(0x1C, 0x00);  // Accelerometer range: +/- 2g.
  writeMPU6500Register(0x1B, 0x00);  // Gyroscope range: +/- 250 deg/s.
  delay(100);

  return true;
}

void writeMPU6500Register(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(MPU6500_ADDR);
  Wire.write(reg);
  Wire.write(value);
  Wire.endTransmission();
}

bool readMPU6500() {
  Wire.beginTransmission(MPU6500_ADDR);
  Wire.write(0x3B);  // Start at ACCEL_XOUT_H register.
  Wire.endTransmission(false);
  Wire.requestFrom(MPU6500_ADDR, (uint8_t)14);

  if (Wire.available() == 14) {
    int16_t rawX = (Wire.read() << 8) | Wire.read();
    int16_t rawY = (Wire.read() << 8) | Wire.read();
    int16_t rawZ = (Wire.read() << 8) | Wire.read();
    Wire.read();
    Wire.read();
    int16_t rawGyroX = (Wire.read() << 8) | Wire.read();
    int16_t rawGyroY = (Wire.read() << 8) | Wire.read();
    int16_t rawGyroZ = (Wire.read() << 8) | Wire.read();

    accelX = rawX / 16384.0;
    accelY = rawY / 16384.0;
    accelZ = rawZ / 16384.0;
    gyroX = rawGyroX / 131.0;
    gyroY = rawGyroY / 131.0;
    gyroZ = rawGyroZ / 131.0;
    return true;
  }

  return false;
}

void startBeaconScanner() {
  xTaskCreatePinnedToCore(
    beaconScanTask,
    "beaconScanTask",
    8192,
    nullptr,
    1,
    nullptr,
    0);

  logLine("Beacon scanner started on core 0.");
}

void beaconScanTask(void *parameter) {
  BLEDevice::init("");
  BLEScan *bleScan = BLEDevice::getScan();
  bleScan->setAdvertisedDeviceCallbacks(new RoomBeaconCallbacks());
  bleScan->setActiveScan(true);
  bleScan->setInterval(100);
  bleScan->setWindow(90);

  while (true) {
    nearestBeaconName[0] = '\0';
    nearestBeaconRssi = -999;

    bleScan->start(BEACON_SCAN_SECONDS, false);

    if (nearestBeaconName[0] != '\0' && nearestBeaconRssi >= BEACON_MIN_RSSI) {
      setCurrentRoom(nearestBeaconName, nearestBeaconRssi);
    } else {
      setCurrentRoom("Outside_Range", 0);
    }

    bleScan->clearResults();
    delay(500);
  }
}

void setCurrentRoom(const char *roomName, int rssi) {
  portENTER_CRITICAL(&roomMux);
  strncpy(currentRoom, roomName, sizeof(currentRoom) - 1);
  currentRoom[sizeof(currentRoom) - 1] = '\0';
  currentRoomRssi = rssi;
  portEXIT_CRITICAL(&roomMux);
}

void getCurrentRoom(char *roomBuffer, size_t roomBufferSize, int *rssi) {
  portENTER_CRITICAL(&roomMux);
  strncpy(roomBuffer, currentRoom, roomBufferSize - 1);
  roomBuffer[roomBufferSize - 1] = '\0';
  *rssi = currentRoomRssi;
  portEXIT_CRITICAL(&roomMux);
}

bool initSDCard() {
  SPI.begin(SD_SCK_PIN, SD_MISO_PIN, SD_MOSI_PIN, SD_CS_PIN);

  if (!SD.begin(SD_CS_PIN)) {
    logLine("SD card mount failed.");
    logLine("Check CS=GPIO10, MOSI=GPIO11, MISO=GPIO12, SCK=GPIO13.");
    return false;
  }

  logLine("SD card ready.");
  return true;
}

void writeSampleFallLog() {
  bool newFile = !SD.exists(FALL_LOG_FILE);
  File dataFile = SD.open(FALL_LOG_FILE, FILE_APPEND);

  if (!dataFile) {
    logLine("Failed to open fall_log.csv.");
    return;
  }

  if (newFile) {
    dataFile.println("AccelX,AccelY,AccelZ,GyroX,GyroY,GyroZ,Status,Room");
  }

  dataFile.println("0.12,-0.05,1.01,0.8,-0.4,0.2,NORMAL,TEST_ROOM");
  dataFile.println("0.20,0.18,0.96,1.2,0.5,-0.3,NORMAL,TEST_ROOM");
  dataFile.println("2.80,-1.40,0.35,120.5,-86.2,45.9,FALL DETECTED,TEST_ROOM");
  dataFile.close();

  logLine("Sample fall_log.csv data written.");
}

void logFallEvent(const char *status) {
  if (!sdReady) {
    return;
  }

  char roomCopy[17];
  int rssiCopy = 0;
  getCurrentRoom(roomCopy, sizeof(roomCopy), &rssiCopy);

  bool newFile = !SD.exists(AI_FALL_LOG_FILE);
  File dataFile = SD.open(AI_FALL_LOG_FILE, FILE_APPEND);

  if (!dataFile) {
    logLine("Failed to open ai_fall_log.csv.");
    return;
  }

  if (newFile) {
    dataFile.println("Status,AIConfidence,AccelX,AccelY,AccelZ,GyroX,GyroY,GyroZ,Room,RSSI");
  }

  dataFile.print(status);
  dataFile.print(",");
  dataFile.print(fallAlertConfidence, 2);
  dataFile.print(",");
  dataFile.print(accelX, 4);
  dataFile.print(",");
  dataFile.print(accelY, 4);
  dataFile.print(",");
  dataFile.print(accelZ, 4);
  dataFile.print(",");
  dataFile.print(gyroX, 4);
  dataFile.print(",");
  dataFile.print(gyroY, 4);
  dataFile.print(",");
  dataFile.print(gyroZ, 4);
  dataFile.print(",");
  dataFile.print(csvSafe(String(roomCopy)));
  dataFile.print(",");
  dataFile.println(rssiCopy);
  dataFile.close();
}

void logLine(const char *message) {
  Serial.println(message);
  // MLDATA is emitted only from logSensorData() after a real IMU read.
  // Emitting it here re-sent the last (or all-zero) reading every time a
  // text message was logged, so the classifier saw stale samples as new.
}

void logMLDataLine() {
  Serial.print("MLDATA,");
  Serial.print(accelX, 4);
  Serial.print(",");
  Serial.print(accelY, 4);
  Serial.print(",");
  Serial.print(accelZ, 4);
  Serial.print(",");
  Serial.print(gyroX, 4);
  Serial.print(",");
  Serial.print(gyroY, 4);
  Serial.print(",");
  Serial.print(gyroZ, 4);
  Serial.println();
}

void logSensorData() {
  char roomCopy[17];
  int rssiCopy = 0;
  getCurrentRoom(roomCopy, sizeof(roomCopy), &rssiCopy);

  String message = "ACC X: " + String(accelX, 2) +
                   " g, Y: " + String(accelY, 2) +
                   " g, Z: " + String(accelZ, 2) +
                   " g | GYRO X: " + String(gyroX, 2) +
                   " dps, Y: " + String(gyroY, 2) +
                   " dps, Z: " + String(gyroZ, 2) +
                   " dps | Room: " + String(roomCopy) +
                   " RSSI: " + String(rssiCopy);

  Serial.println(message);
  logMLDataLine();
}

bool initGSM() {
  if (!detectGSMBaud()) {
    logLine("GSM AT failed.");
    return false;
  }

  String response = sendATCommand("AT", 2000);
  if (response.indexOf("OK") < 0) {
    logLine("GSM AT failed after baud detect.");
    return false;
  }

  sendATCommand("ATE0", 1000);
  sendATCommand("AT+CMGF=1", 1000);
  sendATCommand("AT+CSCS=\"GSM\"", 1000);
  logLine("GSM ready.");
  return true;
}

bool detectGSMBaud() {
  for (uint8_t i = 0; i < sizeof(GSM_BAUD_RATES) / sizeof(GSM_BAUD_RATES[0]); i++) {
    gsmBaud = GSM_BAUD_RATES[i];
    gsmSerial.end();
    delay(200);
    gsmSerial.begin(gsmBaud, SERIAL_8N1, GSM_RX_PIN, GSM_TX_PIN);
    delay(1000);

    while (gsmSerial.available()) {
      gsmSerial.read();
    }

    gsmSerial.println("AT");
    String response = readGSMResponse(1500);

    if (response.indexOf("OK") >= 0) {
      String message = "GSM baud detected: " + String(gsmBaud);
      logLine(message.c_str());
      return true;
    }
  }

  return false;
}

String sendSMS(const char *phoneNumber, const char *message) {
  logLine("Sending GSM test SMS...");

  gsmSerial.print("AT+CMGS=\"");
  gsmSerial.print(phoneNumber);
  gsmSerial.println("\"");

  String prompt = readGSMUntil(5000, ">");
  if (prompt.indexOf(">") < 0) {
    return prompt + " | No SMS prompt";
  }

  gsmSerial.print(message);
  gsmSerial.write(26);

  String response = readGSMUntil(20000, "OK");
  logLine(response.c_str());
  return response;
}

void sendAlertSMSToAll(const char *message) {
  showSendingSmsOnLCD(1);
  sendAndLogSMS(PHONE_NUMBER_1, message);
  showSendingSmsOnLCD(2);
  sendAndLogSMS(PHONE_NUMBER_2, message);
}

void sendAndLogSMS(const char *phoneNumber, const char *message) {
  String smsResponse = sendSMS(phoneNumber, message);
  bool smsSent = isSMSSent(smsResponse);
  String status = String(phoneNumber) + (smsSent ? "_SMS_SENT" : "_SMS_FAILED");
  saveGSMLog(status.c_str(), smsResponse);

  // Show the modem's reply on the LCD when a send fails.
  if (!smsSent) {
    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print("SMS FAIL:");
    lcd.setCursor(0, 1);
    lcd.print(smsResponse.substring(0, 16));
    delay(4000);
  }
}

String sendATCommand(const char *command, uint32_t timeoutMs) {
  gsmSerial.println(command);
  String response = readGSMResponse(timeoutMs);
  logLine(response.c_str());
  return response;
}

String readGSMResponse(uint32_t timeoutMs) {
  String response = "";
  uint32_t startTime = millis();

  while (millis() - startTime < timeoutMs) {
    while (gsmSerial.available()) {
      char incoming = gsmSerial.read();
      response += incoming;
    }
  }

  response.trim();
  return response;
}

// Like readGSMResponse, but returns as soon as `until` (or ERROR) arrives
// instead of always waiting the full timeout.
String readGSMUntil(uint32_t timeoutMs, const char *until) {
  String response = "";
  uint32_t startTime = millis();

  while (millis() - startTime < timeoutMs) {
    while (gsmSerial.available()) {
      response += (char)gsmSerial.read();
    }
    if (response.indexOf(until) >= 0 || response.indexOf("ERROR") >= 0) {
      delay(50);  // let the rest of the line arrive
      while (gsmSerial.available()) {
        response += (char)gsmSerial.read();
      }
      break;
    }
  }

  response.trim();
  return response;
}

bool isSMSSent(const String &response) {
  return response.indexOf("+CMGS:") >= 0 &&
         response.indexOf("OK") >= 0 &&
         response.indexOf("ERROR") < 0;
}

void saveGSMLog(const char *status, const String &response) {
  if (!sdReady) {
    return;
  }

  bool newFile = !SD.exists(GSM_LOG_FILE);
  File dataFile = SD.open(GSM_LOG_FILE, FILE_APPEND);

  if (!dataFile) {
    logLine("Failed to open gsm_log.csv.");
    return;
  }

  if (newFile) {
    dataFile.println("Status,Response");
  }

  dataFile.print(status);
  dataFile.print(",");
  dataFile.println(csvSafe(response));
  dataFile.close();
}

String csvSafe(String value) {
  value.replace("\"", "\"\"");
  value.replace("\r", " ");
  value.replace("\n", " ");
  return "\"" + value + "\"";
}