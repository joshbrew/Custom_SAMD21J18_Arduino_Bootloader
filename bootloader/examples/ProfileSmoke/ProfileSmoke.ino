#include <Arduino.h>
#include <Wire.h>
#include <SPI.h>

#ifdef PIN_LED_TXL
#error The custom profile must leave core USB activity LEDs disabled.
#endif
#ifdef PIN_LED_RXL
#error The custom profile must leave core USB activity LEDs disabled.
#endif
static_assert(PINS_COUNT == 52, "Review the J18 package table if the MCU changes.");
static_assert(J18_HARDWARE_SPI0_VALID && J18_HARDWARE_SPI1_VALID,
              "This compile example expects the reviewed default SPI routes.");

// API compile example only: call after checking the attached hardware's wiring.
void interfaceApiExample() {
  Serial1.begin(115200); // UART0 alias pair.
  Serial2.begin(115200); // UART1 alias pair.
  Wire.begin();
  SPI.begin();
  SPI1.begin();
  analogReadResolution(12);
  (void)analogRead(A0);
}

void setup() {
  Serial.begin(115200); // Native USB; no wait for a client.
  pinMode(LED_BUILTIN, OUTPUT);
}

void loop() {
  digitalWrite(LED_BUILTIN, HIGH);
  delay(500);
  digitalWrite(LED_BUILTIN, LOW);
  delay(500);
}
