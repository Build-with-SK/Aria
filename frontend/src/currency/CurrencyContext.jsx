/**
 * currency/CurrencyContext.jsx
 * Show every price in the viewer's own currency.
 *
 * Detection order: a saved choice, else the browser's region (timezone first,
 * then locale). Someone opening ARIA in Mumbai sees ₹, in London £, in New
 * York $ — and can override it from the sidebar at any time.
 *
 * The rule this module exists to enforce: NEVER convert a number whose native
 * currency is unknown. Prices in this system are native — dollars for AAPL,
 * rupees for RELIANCE.NS, PENCE for HSBA.L — so a blanket "multiply by the USD
 * rate" would be wrong by 88× on an Indian listing and 100× on a London one.
 * When the native currency is unknown, the value is shown as-is and labelled,
 * because an unconverted honest number beats a converted wrong one.
 */
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import axios from 'axios'

const STORAGE_KEY = 'aria-display-currency'

export const CURRENCY_META = {
  USD: { symbol: '$', name: 'US Dollar', locale: 'en-US' },
  INR: { symbol: '₹', name: 'Indian Rupee', locale: 'en-IN' },
  GBP: { symbol: '£', name: 'Pound Sterling', locale: 'en-GB' },
  EUR: { symbol: '€', name: 'Euro', locale: 'de-DE' },
  JPY: { symbol: '¥', name: 'Japanese Yen', locale: 'ja-JP' },
  AUD: { symbol: 'A$', name: 'Australian Dollar', locale: 'en-AU' },
  CAD: { symbol: 'C$', name: 'Canadian Dollar', locale: 'en-CA' },
  CHF: { symbol: 'CHF', name: 'Swiss Franc', locale: 'de-CH' },
  SGD: { symbol: 'S$', name: 'Singapore Dollar', locale: 'en-SG' },
  HKD: { symbol: 'HK$', name: 'Hong Kong Dollar', locale: 'en-HK' },
  AED: { symbol: 'AED', name: 'UAE Dirham', locale: 'en-AE' },
  CNY: { symbol: '¥', name: 'Chinese Yuan', locale: 'zh-CN' },
  ZAR: { symbol: 'R', name: 'South African Rand', locale: 'en-ZA' },
  BRL: { symbol: 'R$', name: 'Brazilian Real', locale: 'pt-BR' },
}

/* Timezone → currency. More reliable than navigator.language, which reports
   the UI language and is frequently en-US on a machine sitting in Mumbai. */
const TZ_CURRENCY = [
  [/^Asia\/(Kolkata|Calcutta)/, 'INR'],
  [/^Europe\/London/, 'GBP'],
  [/^Europe\/Dublin/, 'EUR'],
  [/^Europe\/(Paris|Berlin|Madrid|Rome|Amsterdam|Brussels|Lisbon|Vienna|Helsinki|Athens)/, 'EUR'],
  [/^Europe\/Zurich/, 'CHF'],
  [/^Asia\/Tokyo/, 'JPY'],
  [/^Asia\/(Shanghai|Chongqing)/, 'CNY'],
  [/^Asia\/Hong_Kong/, 'HKD'],
  [/^Asia\/Singapore/, 'SGD'],
  [/^Asia\/Dubai/, 'AED'],
  [/^Australia\//, 'AUD'],
  [/^America\/(Toronto|Vancouver|Edmonton|Winnipeg|Halifax)/, 'CAD'],
  [/^America\/Sao_Paulo/, 'BRL'],
  [/^Africa\/Johannesburg/, 'ZAR'],
  [/^America\//, 'USD'],
]

const LOCALE_CURRENCY = { IN: 'INR', GB: 'GBP', US: 'USD', JP: 'JPY', CN: 'CNY',
  HK: 'HKD', SG: 'SGD', AE: 'AED', AU: 'AUD', CA: 'CAD', ZA: 'ZAR', BR: 'BRL',
  DE: 'EUR', FR: 'EUR', ES: 'EUR', IT: 'EUR', NL: 'EUR', IE: 'EUR', CH: 'CHF' }

export function detectCurrency() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved && CURRENCY_META[saved]) return { code: saved, source: 'your choice' }
  } catch { /* storage blocked — fall through to detection */ }

  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ''
    for (const [re, code] of TZ_CURRENCY) {
      if (re.test(tz)) return { code, source: `detected from your timezone (${tz})` }
    }
  } catch { /* Intl unavailable */ }

  try {
    const region = (navigator.language || '').split('-')[1]
    if (region && LOCALE_CURRENCY[region]) {
      return { code: LOCALE_CURRENCY[region], source: `detected from your locale (${navigator.language})` }
    }
  } catch { /* no navigator */ }

  return { code: 'USD', source: 'default — your region could not be detected' }
}

const CurrencyContext = createContext(null)

export function CurrencyProvider({ children }) {
  const [detected] = useState(detectCurrency)
  const [display, setDisplayState] = useState(detected.code)
  const [rates, setRates] = useState(null)
  const [meta, setMeta] = useState(null)
  const [natives, setNatives] = useState({})     // symbol → native currency

  useEffect(() => {
    axios.get('/api/fx/rates', { params: { base: 'USD' } })
      .then(r => { setRates(r.data?.rates || null); setMeta(r.data || null) })
      .catch(() => { setRates(null) })
  }, [])

  const setDisplay = useCallback((code) => {
    setDisplayState(code)
    try { localStorage.setItem(STORAGE_KEY, code) } catch { /* ignore */ }
  }, [])

  /* Learn the native currency of any symbols we are about to render. Batched
     and cached, so a table of 200 rows costs one request. */
  const resolveNatives = useCallback((symbols) => {
    const want = [...new Set((symbols || []).filter(s => s && !(s in natives)))]
    if (!want.length) return
    axios.get('/api/universe/currencies', { params: { symbols: want.join(',') } })
      .then(r => setNatives(prev => ({ ...prev, ...(r.data?.currencies || {}) })))
      .catch(() => {
        // Mark as unresolved rather than retrying forever; unresolved values
        // are displayed unconverted, which is the safe outcome.
        setNatives(prev => ({ ...prev, ...Object.fromEntries(want.map(s => [s, null])) }))
      })
  }, [natives])

  const value = useMemo(() => {
    const toUSD = (amount, from) => {
      if (amount == null || !from || !rates) return null
      let v = Number(amount)
      let ccy = from
      if (ccy === 'GBp') { v = v / 100; ccy = 'GBP' }   // London quotes in pence
      if (ccy === 'USD') return v
      const rate = rates[ccy]
      return rate ? v / rate : null
    }

    const convert = (amount, from, to = display) => {
      const usd = toUSD(amount, from)
      if (usd == null) return null
      if (to === 'USD') return usd
      const rate = rates?.[to === 'GBp' ? 'GBP' : to]
      if (!rate) return null
      return to === 'GBp' ? usd * rate * 100 : usd * rate
    }

    const fmtIn = (amount, code, digits) => {
      const m = CURRENCY_META[code] || { symbol: '', locale: 'en-US' }
      const n = Number(amount)
      const abs = Math.abs(n)
      // Sub-unit prices (penny stocks, most crypto pairs) need more decimals;
      // an exact zero does not — "£0.0000" reads like a precision claim.
      const d = digits != null ? digits
        : abs === 0 ? 2
        : abs >= 1000 ? 0
        : abs >= 1 ? 2
        : 4
      // The sign goes OUTSIDE the symbol: -£69,498, never £-69,498.
      const sign = n < 0 ? '-' : ''
      return sign + m.symbol + abs.toLocaleString(m.locale, {
        minimumFractionDigits: d, maximumFractionDigits: d,
      })
    }

    /* The one function the UI should call.
       Returns { text, converted, native, title } — `converted` is false when
       the value is being shown in its own currency untouched. */
    const price = (amount, opts = {}) => {
      const { symbol, from, digits } = opts
      if (amount == null || Number.isNaN(Number(amount))) {
        return { text: '—', converted: false, native: null, title: 'no value' }
      }
      const nativeCcy = from || (symbol ? natives[symbol] : null)

      if (!nativeCcy) {
        return {
          text: Number(amount).toLocaleString(undefined, { maximumFractionDigits: 2 }),
          converted: false, native: null,
          title: symbol
            ? `Quote currency for ${symbol} is unknown — shown unconverted rather than guessed`
            : 'Currency unknown — shown unconverted',
        }
      }
      if (!rates) {
        return { text: fmtIn(nativeCcy === 'GBp' ? Number(amount) / 100 : Number(amount),
                             nativeCcy === 'GBp' ? 'GBP' : nativeCcy, digits),
                 converted: false, native: nativeCcy, title: 'FX rates unavailable — native currency' }
      }

      const out = convert(amount, nativeCcy, display)
      if (out == null) {
        const shown = nativeCcy === 'GBp' ? Number(amount) / 100 : Number(amount)
        const code = nativeCcy === 'GBp' ? 'GBP' : nativeCcy
        return { text: fmtIn(shown, code, digits), converted: false, native: nativeCcy,
                 title: `No ${display} rate for ${code} — shown in its native currency` }
      }
      const nativeShown = fmtIn(nativeCcy === 'GBp' ? Number(amount) / 100 : Number(amount),
                                nativeCcy === 'GBp' ? 'GBP' : nativeCcy, digits)
      const isNative = (nativeCcy === display) || (nativeCcy === 'GBp' && display === 'GBP')
      return {
        text: fmtIn(out, display, digits),
        converted: !isNative,
        native: nativeCcy,
        title: isNative ? `Quoted in ${nativeCcy}`
          : `${nativeShown} ${nativeCcy === 'GBp' ? '(quoted in pence)' : ''} converted at `
            + `1 USD = ${rates[display] ?? '—'} ${display} · ${meta?.as_of || ''} · display only`,
      }
    }

    return { display, setDisplay, detected, rates, meta, natives, resolveNatives, convert, price }
  }, [display, setDisplay, detected, rates, meta, natives, resolveNatives])

  return <CurrencyContext.Provider value={value}>{children}</CurrencyContext.Provider>
}

export function useCurrency() {
  const ctx = useContext(CurrencyContext)
  if (!ctx) throw new Error('useCurrency must be used inside <CurrencyProvider>')
  return ctx
}

/** Render one price in the viewer's currency. */
export function Price({ value, symbol, from, digits, style, showNativeHint = false }) {
  const { price } = useCurrency()
  const p = price(value, { symbol, from, digits })
  return (
    <span title={p.title} style={style}>
      {p.text}
      {showNativeHint && p.converted && (
        <span style={{ opacity: 0.45, fontSize: '0.82em', marginLeft: 3 }}>≈</span>
      )}
    </span>
  )
}

/** Registers a list of symbols so their native currencies get resolved. */
export function useResolveCurrencies(symbols) {
  const { resolveNatives } = useCurrency()
  const key = (symbols || []).join(',')
  useEffect(() => {
    if (key) resolveNatives(key.split(','))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
}
