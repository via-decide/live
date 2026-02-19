
function sma(values, period) {
  if (values.length < period) return null;
  const slice = values.slice(values.length - period);
  const sum = slice.reduce((a, b) => a + b, 0);
  return sum / period;
}

function ema(values, period) {
  if (values.length < period) return null;
  const k = 2 / (period + 1);
  // seed with SMA
  let e = values.slice(0, period).reduce((a,b)=>a+b,0) / period;
  for (let i = period; i < values.length; i++) {
    e = values[i] * k + e * (1 - k);
  }
  return e;
}

function trueRange(prevClose, high, low) {
  if (prevClose == null) return high - low;
  return Math.max(
    high - low,
    Math.abs(high - prevClose),
    Math.abs(low - prevClose)
  );
}

function atr14(bars) {
  // bars: [{h,l,c,pc}]
  const period = 14;
  if (bars.length < period + 1) return null;

  const trs = [];
  for (let i = 0; i < bars.length; i++) {
    const b = bars[i];
    const prevClose = i === 0 ? null : bars[i - 1].c;
    trs.push(trueRange(prevClose, b.h, b.l));
  }

  // Wilder's smoothing
  let atr = trs.slice(0, period).reduce((a,b)=>a+b,0) / period;
  for (let i = period; i < trs.length; i++) {
    atr = (atr * (period - 1) + trs[i]) / period;
  }
  return atr;
}

function zscore(values, lookback) {
  if (values.length < lookback) return null;
  const slice = values.slice(values.length - lookback);
  const mean = slice.reduce((a,b)=>a+b,0) / lookback;
  const varr = slice.reduce((a,b)=>a + (b-mean)*(b-mean), 0) / lookback;
  const sd = Math.sqrt(varr);
  if (sd === 0) return 0;
  return (values[values.length - 1] - mean) / sd;
}

module.exports = { sma, ema, atr14, zscore };
