#include "Particle.h"
#include <Wire.h>
#include "PowerShield.h"

SYSTEM_MODE(AUTOMATIC);

const char* server = "10.0.1.5";
const int port = 3000;
const char* endpoint = "/battery-data";
const bool debugMode = true;

const int tempPin = A3;
PowerShield batteryMonitor;
bool isFuelGaugeInitialized = false;

float lastSoc = 0.0;
float lastTemperature = 22.0;
const float minValidVoltage = 3.0;
const uint16_t batteryCapacity = 2000;
float batteryVoltage = 0.0;

// Voltage slope detection for charging state
float previousVoltage = 0.0;
bool isCharging = false;
const float voltageSpikeThreshold = 0.05;
const float voltageDropThreshold = -0.05;
const float socDeltaThreshold = 0.05;
const float chargingVoltageThreshold = 3.7;

void configureFuelGauge() {
  uint16_t configValue = (batteryCapacity / 10) << 8;
  Wire.beginTransmission(0x36);
  Wire.write(0x0C);
  Wire.write((configValue >> 8) & 0xFF);
  Wire.write(configValue & 0xFF);
  Wire.endTransmission();
}

float estimateSocFromVoltage(float voltage) {
  if (voltage >= 4.2) return 100.0;
  if (voltage <= 3.0) return 0.0;
  return ((voltage - 3.0) / (4.2 - 3.0)) * 100.0;
}

void setup() {
  Serial.begin(9600);
  Wire.begin();
  pinMode(tempPin, INPUT);
  
  if (batteryMonitor.begin()) {
    batteryMonitor.reset();
    delay(2000);
    batteryMonitor.quickStart();
    configureFuelGauge();
    isFuelGaugeInitialized = true;
    
    batteryVoltage = batteryMonitor.getVCell();
    previousVoltage = batteryVoltage;
    isCharging = (batteryVoltage >= chargingVoltageThreshold);
  }
}

void loop() {
  float soc = 0.0;
  if (isFuelGaugeInitialized) {
    batteryVoltage = batteryMonitor.getVCell();
    soc = batteryMonitor.getSoC();
    
    float socDelta = abs(soc - lastSoc);
    if (soc > 99.0 || socDelta > socDeltaThreshold || (soc == 0.0 && batteryVoltage > minValidVoltage)) {
      for (int i = 0; i < 5; i++) {
        batteryMonitor.reset();
        delay(2000);
      }
      batteryMonitor.quickStart();
      configureFuelGauge();
      soc = batteryMonitor.getSoC();
      if (soc == 0.0 && batteryVoltage > minValidVoltage) {
        soc = estimateSocFromVoltage(batteryVoltage);
      }
    }
    soc = min(soc, 99.0);
    lastSoc = soc;
  }

  int rawADC = analogRead(tempPin);
  float voltageTemp = (rawADC * 3.3) / 4095.0;
  float temperatureC = (voltageTemp - 0.5) * 100.0;
  
  if (temperatureC < 15.0 || temperatureC > 35.0) {
    temperatureC = lastTemperature;
  } else {
    lastTemperature = temperatureC;
  }

  // Detect charging state using voltage slope
  float voltageDelta = batteryVoltage - previousVoltage;
  if (voltageDelta >= voltageSpikeThreshold) {
    isCharging = true;
  } else if (voltageDelta <= voltageDropThreshold) {
    isCharging = false;
  }
  previousVoltage = batteryVoltage;

  String timestamp = Time.format(Time.now(), TIME_FORMAT_ISO8601_FULL);
  String payload = String::format(
    "{\"timestamp\":\"%s\",\"voltage\":%.2f,\"soc\":%.2f,\"temperature\":%.1f}",
    timestamp.c_str(), batteryVoltage, soc, temperatureC
  );
  TCPClient client;
  if (client.connect(server, port)) {
    client.println("POST " + String(endpoint) + " HTTP/1.1");
    client.println("Host: " + String(server));
    client.println("Content-Type: application/json");
    client.println("Content-Length: " + String(payload.length()));
    client.println();
    client.println(payload);
    client.flush();
    delay(100);
    client.stop();
  }
  delay(1000);
}