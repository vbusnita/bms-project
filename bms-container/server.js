const express = require('express');
const { MongoClient } = require('mongodb');
const WebSocket = require('ws');
const app = express();
const port = 3000;

// MongoDB connection details
const mongoUrl = 'mongodb://adminbms:buchis@10.0.1.6:27017/bms?authSource=admin';
const dbName = 'bms';
const collectionName = 'battery_data';

app.use(express.json());
app.use(express.static('public'));

let db;

// Connect to MongoDB
MongoClient.connect(mongoUrl, { useUnifiedTopology: true })
  .then(client => {
    console.log('Connected to MongoDB.');
    db = client.db(dbName);
    // Ensure the collection exists (MongoDB creates it on first insert, but this confirms)
    db.createCollection(collectionName, (err) => {
      if (err) console.error('Error creating collection:', err.message);
      else console.log('Collection battery_data ensured.');
    });
  })
  .catch(err => {
    console.error('MongoDB connection error:', err.message);
    process.exit(1);
  });

app.post('/battery-data', async (req, res) => {
  console.log('Received request:', req.method, req.headers, req.body);
  if (!req.body) {
    return res.status(400).json({ error: 'Missing body' });
  }
  const { timestamp, voltage, soc, temperature } = req.body;
  if (!timestamp || voltage === undefined || soc === undefined) {
    return res.status(400).json({ error: 'Missing required fields' });
  }

  // Determine is_charging based on voltage slope (temporary until INA219 is added)
  const isCharging = await determineIsCharging(voltage);

  const data = {
    timestamp: new Date(timestamp),
    voltage: parseFloat(voltage),
    soc: parseFloat(soc),
    temperature: temperature ? parseFloat(temperature) : null,
    charging_current: null,  // Placeholder for INA219
    discharging_current: null,  // Placeholder for INA219
    is_charging: isCharging,
    battery_health: null,  // Placeholder for future SOH
    cycle_count: null  // Placeholder for future cycle tracking
  };

  try {
    await db.collection(collectionName).insertOne(data);
    wss.clients.forEach(client => {
      if (client.readyState === WebSocket.OPEN) {
        client.send(JSON.stringify(data));
      }
    });
    res.status(200).json({ message: 'Data received' });
  } catch (err) {
    console.error('Insert error:', err.message);
    res.status(500).json({ error: 'Database error', details: err.message });
  }
});

app.get('/latest-data', async (req, res) => {
  try {
    const row = await db.collection(collectionName)
      .find()
      .sort({ timestamp: -1 })
      .limit(1)
      .toArray();
    res.json(row[0] || {});
  } catch (err) {
    console.error('Query error:', err.message);
    res.status(500).json({ error: 'Database error', details: err.message });
  }
});

app.get('/historical-data', async (req, res) => {
  try {
    const rows = await db.collection(collectionName)
      .find()
      .sort({ timestamp: 1 })
      .toArray();
    res.json(rows || []);
  } catch (err) {
    console.error('Query error:', err.message);
    res.status(500).json({ error: 'Database error', details: err.message });
  }
});

// Temporary function to determine is_charging based on voltage slope (until INA219 is added)
async function determineIsCharging(currentVoltage) {
  try {
    const lastTwoRecords = await db.collection(collectionName)
      .find()
      .sort({ timestamp: -1 })
      .limit(2)
      .toArray();
    if (lastTwoRecords.length < 2) return true;  // Default to charging if not enough data
    const previousVoltage = lastTwoRecords[1].voltage;
    const voltageDelta = currentVoltage - previousVoltage;
    return voltageDelta >= 0.05;  // Threshold lowered to match firmware
  } catch (err) {
    console.error('Error determining is_charging:', err.message);
    return true;  // Default to charging on error
  }
}

const server = app.listen(port, () => console.log(`Server running on port ${port}`));
const wss = new WebSocket.Server({ server });

wss.on('connection', (ws) => {
  console.log('WebSocket client connected');
  ws.on('close', () => console.log('WebSocket client disconnected'));
});