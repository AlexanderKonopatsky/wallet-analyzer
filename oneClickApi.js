/**
 * 1Click API Client
 * Based on swap-cli, adapted for payment service
 */

const API_BASE = 'https://1click.chaindefuser.com';

function getHeaders() {
  const headers = { 'Content-Type': 'application/json' };
  const token = process.env.ONECLICK_JWT;
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return headers;
}

async function apiRequest(endpoint, options = {}) {
  const url = `${API_BASE}${endpoint}`;
  const response = await fetch(url, {
    ...options,
    headers: { ...getHeaders(), ...options.headers }
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`API Error (${response.status}): ${errorText}`);
  }

  return response.json();
}

export async function getTokens() {
  return apiRequest('/v0/tokens');
}

export function findToken(tokens, chain, tokenId) {
  const chainLower = chain.toLowerCase();
  const tokenLower = tokenId.toLowerCase();

  return tokens.find(t => {
    const matchesChain = t.blockchain?.toLowerCase() === chainLower ||
                         t.chain?.toLowerCase() === chainLower;
    if (!matchesChain) return false;

    return t.symbol?.toLowerCase() === tokenLower ||
           t.contractAddress?.toLowerCase() === tokenLower ||
           t.address?.toLowerCase() === tokenLower ||
           t.assetId?.toLowerCase() === tokenLower ||
           t.defuseAssetId?.toLowerCase().includes(tokenLower);
  });
}

export async function getQuote({
  dry = false,
  originAsset,
  destinationAsset,
  amount,
  recipient,
  refundTo,
  slippageTolerance = 100,
  deadline
}) {
  const body = {
    dry,
    swapType: 'EXACT_INPUT',
    slippageTolerance,
    originAsset,
    depositType: 'ORIGIN_CHAIN',
    destinationAsset,
    amount: String(amount),
    refundTo,
    refundType: 'ORIGIN_CHAIN',
    recipient,
    recipientType: 'DESTINATION_CHAIN',
    deadline,
    quoteWaitingTimeMs: 5000
  };

  return apiRequest('/v0/quote', {
    method: 'POST',
    body: JSON.stringify(body)
  });
}

export async function getExecutionStatus(depositAddress) {
  return apiRequest(`/v0/status?depositAddress=${encodeURIComponent(depositAddress)}`);
}

export const STATUS_DESCRIPTIONS = {
  PENDING_DEPOSIT: 'Waiting for deposit...',
  KNOWN_DEPOSIT_TX: 'Deposit detected, confirming...',
  INCOMPLETE_DEPOSIT: 'Deposit incomplete',
  PROCESSING: 'Processing swap...',
  SUCCESS: 'Payment received!',
  FAILED: 'Payment failed',
  REFUNDED: 'Refunded to sender'
};
