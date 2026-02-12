/**
 * Simple JSON file database
 */

import { readFileSync, writeFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const DB_PATH = join(__dirname, '..', 'data', 'payments.json');

function readDb() {
  if (!existsSync(DB_PATH)) return [];
  const data = readFileSync(DB_PATH, 'utf-8');
  return JSON.parse(data);
}

function writeDb(data) {
  writeFileSync(DB_PATH, JSON.stringify(data, null, 2), 'utf-8');
}

export function createPayment(payment) {
  const payments = readDb();
  payment.id = 'pay_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
  payment.createdAt = new Date().toISOString();
  payment.status = 'PENDING_DEPOSIT';
  payment.completedAt = null;
  payments.push(payment);
  writeDb(payments);
  return payment;
}

export function getPayment(id) {
  const payments = readDb();
  return payments.find(p => p.id === id) || null;
}

export function getPaymentByDeposit(depositAddress) {
  const payments = readDb();
  return payments.find(p => p.depositAddress === depositAddress) || null;
}

export function updatePayment(id, updates) {
  const payments = readDb();
  const index = payments.findIndex(p => p.id === id);
  if (index === -1) return null;
  Object.assign(payments[index], updates);
  writeDb(payments);
  return payments[index];
}

export function getAllPayments() {
  return readDb();
}
