import 'dotenv/config';
import express from 'express';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import {
  getTokens,
  findToken,
  getQuote,
  getExecutionStatus,
  STATUS_DESCRIPTIONS
} from './lib/oneClickApi.js';
import {
  parseTokenId,
  toBaseUnits,
  fromBaseUnits,
  getDeadline,
  getChainType,
  isValidAddress
} from './lib/utils.js';
import {
  createPayment,
  getPayment,
  updatePayment,
  getAllPayments
} from './lib/db.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const app = express();

app.use(express.json());
app.use(express.static(join(__dirname, 'public')));

// Config from .env
const RECEIVE_ADDRESS = process.env.RECEIVE_ADDRESS;
const RECEIVE_TOKEN = process.env.RECEIVE_TOKEN || 'base:usdc';

if (!RECEIVE_ADDRESS) {
  console.error('ERROR: RECEIVE_ADDRESS not set in .env');
  process.exit(1);
}

const receiveParsed = parseTokenId(RECEIVE_TOKEN);

// Cache tokens for 5 minutes
let tokensCache = null;
let tokensCacheTime = 0;
const CACHE_TTL = 5 * 60 * 1000;

async function getCachedTokens() {
  if (tokensCache && Date.now() - tokensCacheTime < CACHE_TTL) {
    return tokensCache;
  }
  tokensCache = await getTokens();
  tokensCacheTime = Date.now();
  return tokensCache;
}

// GET /api/tokens - list supported tokens grouped by chain
app.get('/api/tokens', async (req, res) => {
  try {
    const tokens = await getCachedTokens();
    console.log(`[/api/tokens] Got ${tokens.length} tokens from API`);

    // Filter only stablecoins
    const stablecoins = ['USDC', 'USDT', 'DAI'];
    const filtered = tokens.filter(t => stablecoins.includes(t.symbol));

    // Group by chain
    const grouped = {};
    for (const t of filtered) {
      const chain = t.blockchain || t.chain || 'unknown';
      if (!grouped[chain]) grouped[chain] = [];
      grouped[chain].push({
        symbol: t.symbol,
        name: t.name || '',
        decimals: t.decimals,
        chain,
        defuseAssetId: t.defuseAssetId || t.assetId,
        contractAddress: t.contractAddress || t.address || null
      });
    }

    console.log(`[/api/tokens] Filtered to ${filtered.length} stablecoins, grouped into ${Object.keys(grouped).length} chains`);
    res.json({ tokens: grouped });
  } catch (error) {
    console.error('[/api/tokens] ERROR:', error);
    res.status(500).json({ error: error.message });
  }
});

// POST /api/quote - get a dry quote (preview)
app.post('/api/quote', async (req, res) => {
  try {
    const { amount, originToken, refundAddress } = req.body;

    if (!amount || !originToken || !refundAddress) {
      return res.status(400).json({ error: 'amount, originToken, and refundAddress are required' });
    }

    const tokens = await getCachedTokens();

    // Find origin token
    const fromParsed = parseTokenId(originToken);
    const fromToken = findToken(tokens, fromParsed.chain, fromParsed.token);
    if (!fromToken) {
      return res.status(400).json({ error: `Token not found: ${originToken}` });
    }

    // Find destination token
    const toToken = findToken(tokens, receiveParsed.chain, receiveParsed.token);
    if (!toToken) {
      return res.status(500).json({ error: `Destination token not configured correctly` });
    }

    // Validate refund address
    const fromChainType = getChainType(fromParsed.chain);
    if (!isValidAddress(refundAddress, fromChainType)) {
      return res.status(400).json({ error: `Invalid refund address for ${fromParsed.chain}` });
    }

    // User sends exact amount (EXACT_INPUT)
    const originAmountBase = toBaseUnits(amount, fromToken.decimals);

    // Get quote
    const dryQuote = await getQuote({
      dry: true,
      originAsset: fromToken.defuseAssetId || fromToken.assetId,
      destinationAsset: toToken.defuseAssetId || toToken.assetId,
      amount: originAmountBase,
      recipient: RECEIVE_ADDRESS,
      refundTo: refundAddress,
      slippageTolerance: 100,
      deadline: getDeadline(1)
    });

    const quoteData = dryQuote.quote || dryQuote;

    res.json({
      originToken: originToken,
      originSymbol: fromToken.symbol,
      originChain: fromParsed.chain,
      originAmount: amount,
      originDecimals: fromToken.decimals,
      destinationAmount: quoteData.amountOut ? fromBaseUnits(quoteData.amountOut, toToken.decimals) : '0',
      destinationSymbol: toToken.symbol,
      destinationChain: receiveParsed.chain,
      feeUsd: quoteData.feeUsd || null
    });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

// POST /api/payment/create - create a payment with deposit address
app.post('/api/payment/create', async (req, res) => {
  try {
    const { amount, originToken, refundAddress, originAmount } = req.body;

    if (!amount || !originToken || !refundAddress || !originAmount) {
      return res.status(400).json({ error: 'amount, originToken, refundAddress, and originAmount are required' });
    }

    const tokens = await getCachedTokens();

    const fromParsed = parseTokenId(originToken);
    const fromToken = findToken(tokens, fromParsed.chain, fromParsed.token);
    if (!fromToken) {
      return res.status(400).json({ error: `Token not found: ${originToken}` });
    }

    const toToken = findToken(tokens, receiveParsed.chain, receiveParsed.token);
    if (!toToken) {
      return res.status(500).json({ error: 'Destination token not configured' });
    }

    const fromChainType = getChainType(fromParsed.chain);
    if (!isValidAddress(refundAddress, fromChainType)) {
      return res.status(400).json({ error: `Invalid refund address for ${fromParsed.chain}` });
    }

    const originAmountBase = toBaseUnits(originAmount, fromToken.decimals);

    // Get real quote (non-dry) to create deposit address
    const quoteResponse = await getQuote({
      dry: false,
      originAsset: fromToken.defuseAssetId || fromToken.assetId,
      destinationAsset: toToken.defuseAssetId || toToken.assetId,
      amount: originAmountBase,
      recipient: RECEIVE_ADDRESS,
      refundTo: refundAddress,
      slippageTolerance: 100,
      deadline: getDeadline(1)
    });

    const quoteData = quoteResponse.quote || quoteResponse;

    if (!quoteData.depositAddress) {
      return res.status(500).json({ error: 'No deposit address received' });
    }

    // Save to database
    const payment = createPayment({
      amount,
      originAmount,
      originAmountBase,
      originToken,
      originSymbol: fromToken.symbol,
      originChain: fromParsed.chain,
      originDecimals: fromToken.decimals,
      destinationToken: RECEIVE_TOKEN,
      destinationSymbol: toToken.symbol,
      depositAddress: quoteData.depositAddress,
      refundAddress,
      amountOut: quoteData.amountOut ? fromBaseUnits(quoteData.amountOut, toToken.decimals) : amount
    });

    res.json({
      id: payment.id,
      depositAddress: quoteData.depositAddress,
      originAmount,
      originSymbol: fromToken.symbol,
      originChain: fromParsed.chain,
      amountOut: payment.amountOut,
      destinationSymbol: toToken.symbol,
      status: payment.status
    });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

// GET /api/payment/:id/status - check payment status
app.get('/api/payment/:id/status', async (req, res) => {
  try {
    const payment = getPayment(req.params.id);
    if (!payment) {
      return res.status(404).json({ error: 'Payment not found' });
    }

    // If already in terminal state, return from db
    if (['SUCCESS', 'FAILED', 'REFUNDED'].includes(payment.status)) {
      return res.json({
        id: payment.id,
        status: payment.status,
        statusDescription: STATUS_DESCRIPTIONS[payment.status],
        ...payment
      });
    }

    // Poll 1Click API for current status
    try {
      const apiStatus = await getExecutionStatus(payment.depositAddress);

      if (apiStatus.status !== payment.status) {
        const updates = { status: apiStatus.status };
        if (['SUCCESS', 'FAILED', 'REFUNDED'].includes(apiStatus.status)) {
          updates.completedAt = new Date().toISOString();
        }
        if (apiStatus.swapDetails) {
          updates.swapDetails = apiStatus.swapDetails;
        }
        updatePayment(payment.id, updates);
        Object.assign(payment, updates);
      }

      res.json({
        id: payment.id,
        status: payment.status,
        statusDescription: STATUS_DESCRIPTIONS[payment.status] || payment.status,
        originAmount: payment.originAmount,
        originSymbol: payment.originSymbol,
        originChain: payment.originChain,
        amountOut: payment.amountOut,
        destinationSymbol: payment.destinationSymbol,
        depositAddress: payment.depositAddress,
        refundAddress: payment.refundAddress,
        createdAt: payment.createdAt,
        completedAt: payment.completedAt,
        swapDetails: payment.swapDetails || null
      });
    } catch (apiError) {
      // If API call fails, return last known status
      res.json({
        id: payment.id,
        status: payment.status,
        statusDescription: STATUS_DESCRIPTIONS[payment.status] || payment.status,
        ...payment,
        apiError: apiError.message
      });
    }
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

// GET /api/payments - list all payments (admin)
app.get('/api/payments', (req, res) => {
  const payments = getAllPayments();
  res.json({ payments: payments.reverse() });
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Pay Service running at http://localhost:${PORT}`);
  console.log(`Receiving ${RECEIVE_TOKEN} to ${RECEIVE_ADDRESS}`);
});
