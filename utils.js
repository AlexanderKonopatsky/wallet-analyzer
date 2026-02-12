/**
 * Utility functions
 * Based on swap-cli
 */

export function toBaseUnits(amountStr, decimals) {
  if (!amountStr || isNaN(Number(amountStr))) return '0';
  const [intPart, fracPart = ''] = amountStr.split('.');
  const fracPadded = (fracPart + '0'.repeat(decimals)).slice(0, decimals);
  const normalized = `${BigInt(intPart || '0')}${fracPadded}`;
  const result = normalized.replace(/^0+(?=\d)/, '');
  return result.length ? result : '0';
}

export function fromBaseUnits(baseUnits, decimals) {
  if (!baseUnits || baseUnits === '0') return '0';
  const str = baseUnits.toString().padStart(decimals + 1, '0');
  const intPart = str.slice(0, -decimals) || '0';
  const fracPart = str.slice(-decimals).replace(/0+$/, '');
  return fracPart ? `${intPart}.${fracPart}` : intPart;
}

export function parseTokenId(tokenStr) {
  const parts = tokenStr.split(':');
  if (parts.length < 2) {
    throw new Error(`Invalid token format: ${tokenStr}. Expected: chain:token`);
  }
  return {
    chain: parts[0].toLowerCase(),
    token: parts.slice(1).join(':')
  };
}

export function getDeadline(hoursFromNow = 1) {
  const deadline = new Date();
  deadline.setHours(deadline.getHours() + hoursFromNow);
  return deadline.toISOString();
}

export function getChainType(chainId) {
  const chain = chainId.toLowerCase();
  if (chain === 'near') return 'near';
  if (chain === 'sol' || chain === 'solana') return 'solana';
  if (chain === 'aptos') return 'aptos';
  if (chain === 'sui') return 'sui';
  if (chain === 'ton') return 'ton';
  if (chain === 'stellar') return 'stellar';
  if (chain === 'tron') return 'tron';
  return 'evm'; // eth, arb, base, pol, bsc, avax, op, gnosis, etc.
}

export function isValidAddress(address, chainType) {
  if (!address) return false;
  switch (chainType) {
    case 'near':
      return /^[a-z0-9._-]{2,64}$/.test(address) || /^[0-9a-f]{64}$/.test(address);
    case 'evm':
      return /^0x[a-fA-F0-9]{40}$/.test(address);
    case 'solana':
      return /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(address);
    case 'aptos':
      return /^0x[a-fA-F0-9]{64}$/.test(address);
    case 'sui':
      return /^0x[a-fA-F0-9]{64}$/.test(address);
    case 'ton':
      return /^[a-zA-Z0-9_-]{48}$/.test(address) || /^[UEk][Qf][a-zA-Z0-9_-]{46}$/.test(address);
    case 'tron':
      return /^T[a-zA-Z0-9]{33}$/.test(address);
    case 'stellar':
      return /^G[A-Z0-9]{55}$/.test(address);
    default:
      return address.length > 5; // Basic fallback
  }
}

export const CHAINS = {
  near: { id: 'near', name: 'NEAR' },
  ethereum: { id: 'ethereum', name: 'Ethereum' },
  base: { id: 'base', name: 'Base' },
  arbitrum: { id: 'arbitrum', name: 'Arbitrum' },
  polygon: { id: 'polygon', name: 'Polygon' },
  solana: { id: 'solana', name: 'Solana' },
  bsc: { id: 'bsc', name: 'BNB Chain' },
  avalanche: { id: 'avalanche', name: 'Avalanche' }
};
