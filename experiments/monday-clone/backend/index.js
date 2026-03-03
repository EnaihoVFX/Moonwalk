const express = require('express');
const { PrismaClient } = require('@prisma/client');
const cors = require('cors');

const prisma = new PrismaClient();
const app = express();

app.use(cors());
app.use(express.json());

// Health Check
app.get('/health', (req, res) => {
  res.json({ status: 'ok', timestamp: new Date() });
});

// --- Boards ---
app.get('/api/boards', async (req, res) => {
  const boards = await prisma.board.findMany({
    include: { groups: { include: { items: { include: { values: true } } } }, columns: true }
  });
  res.json(boards);
});

app.post('/api/boards', async (req, res) => {
  const { name, workspaceId } = req.body;
  try {
    const board = await prisma.board.create({
      data: {
        name,
        workspaceId,
        groups: {
          create: [
            { title: 'Group 1', color: '#579bfc' },
            { title: 'Group 2', color: '#a25ddc' }
          ]
        },
        columns: {
          create: [
            { title: 'Status', type: 'status', order: 0 },
            { title: 'Date', type: 'date', order: 1 }
          ]
        }
      }
    });
    res.status(201).json(board);
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

// --- Items ---
app.post('/api/items', async (req, res) => {
  const { name, groupId } = req.body;
  try {
    const item = await prisma.item.create({
      data: { name, groupId }
    });
    res.status(201).json(item);
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

const PORT = process.env.PORT || 3001;
app.listen(PORT, () => {
  console.log(`Server running on http://localhost:${PORT}`);
});
