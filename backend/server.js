// server.js
import express from 'express';
import path from 'path';
import fs from 'fs';
import mammoth from 'mammoth';
import { GoogleGenAI } from '@google/genai';
import cors from 'cors';
import dotenv from 'dotenv';

dotenv.config();

const app = express();
app.use(cors());
app.use(express.json());
app.use(express.static(process.cwd())); // serve index.html & assets

// 1) Instantiate the GoogleGenAI client
const ai = new GoogleGenAI({
  apiKey: process.env.GOOGLE_API_KEY
});

// 2) Load the entire DOCX text at startup
let fullText = '';
(async () => {
  try {
    const buffer = fs.readFileSync(
      path.join(process.cwd(), 'Anfarnd Employee Policy - 2025.docx' )
    );
    const { value: rawText } = await mammoth.extractRawText({ buffer });
    fullText = rawText.trim();
    console.log(`Loaded document text (${fullText.length} chars).`);
  } catch (err) {
    console.error('Failed to load DOCX:', err);
  }
})();

// 3) Chat endpoint
app.post('/chat', async (req, res) => {
  const { question } = req.body;
  if (!question) {
    return res.status(400).json({ error: 'No question provided' });
  }

  // Build the prompt embedding the entire document
  const prompt = `
You are a helpful assistant.make your response humanlike. Use the following document to inform your answer:

${fullText}

User: ${question}
Assistant:
`.trim();

  try {
    // 4) Call Gemini via GoogleGenAI
    const response = await ai.models.generateContent({
      model: 'gemini-2.0-flash',
      contents: prompt
    });

    const answer = response.text?.trim();
    if (!answer) throw new Error('No answer returned');

    return res.json({ answer, source: 'gemini-2.0-flash' });
  } catch (err) {
    console.error('GoogleGenAI error:', err);
    return res.status(500).json({ error: 'GoogleGenAI API failure' });
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`🟢 Server listening at http://localhost:${PORT}`);
});
