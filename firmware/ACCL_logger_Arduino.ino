const int ap1 = A5;
const int ap2 = A4;
const int ap3 = A3;

void setup() {
  // Increase baud rate for higher throughput
  Serial.begin(115200);
}

void loop() {
  // Read the analog input values:
  int sv1 = analogRead(ap1);
  int sv2 = analogRead(ap2);
  int sv3 = analogRead(ap3);

  // Print the data to serial monitor in CSV format:
  Serial.print(sv1);
  Serial.print(",");
  Serial.print(sv2);
  Serial.print(",");
  Serial.println(sv3);

  // No delays => maximum possible loop rate
}
