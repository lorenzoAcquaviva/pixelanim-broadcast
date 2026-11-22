/* Lightweight client-side internationalization. Italian is the source language. */
(() => {
  const SUPPORTED = ['it', 'en'];
  const STORAGE_KEY = 'pixelanim.language';
  const dictionaries = {};
  let current = 'it';
  let observer;
  const originalText = new WeakMap();
  const lastText = new WeakMap();
  const originalAttrs = new WeakMap();

  const patterns = [
    [/^Errore PixelAnim: (.*)$/u, {it:'Errore PixelAnim: $1',en:'PixelAnim error: $1'}],
    [/^Errore board: (.*)$/u, {it:'Errore board: $1',en:'Board error: $1'}],
    [/^Errore salvataggio: (.*)$/u, {it:'Errore salvataggio: $1',en:'Save error: $1'}],
    [/^Errore caricamento: (.*)$/u, {it:'Errore caricamento: $1',en:'Loading error: $1'}],
    [/^Errore avvio: (.*)$/u, {it:'Errore avvio: $1',en:'Start error: $1'}],
    [/^Errore STOP: (.*)$/u, {it:'Errore STOP: $1',en:'STOP error: $1'}],
    [/^In riproduzione: (\d+) frame/u, {it:'In riproduzione: $1 frame',en:'Playing: $1 frames'}],
    [/^(\d+) frame per ciclo$/u, {it:'$1 frame per ciclo',en:'$1 frames per cycle'}],
    [/^PixelAnim non raggiungibile: (.*)$/u, {it:'PixelAnim non raggiungibile: $1',en:'PixelAnim is unreachable: $1'}],
    [/^Errore frame: (.*)$/u, {it:'Errore frame: $1',en:'Frame error: $1'}],
    [/^Errore render: (.*)$/u, {it:'Errore render: $1',en:'Render error: $1'}],
    [/^Servono da 1 a (\d+) grafi$/u, {it:'Servono da 1 a $1 grafi',en:'Between 1 and $1 graphs are required'}],
    [/^Board non trovata: (.*)$/u, {it:'Board non trovata: $1',en:'Board not found: $1'}],
    [/^Board (\d+)×(\d+) non supportata dal firmware$/u, {it:'Board $1×$2 non supportata dal firmware',en:'Board $1×$2 is not supported by the firmware'}],
    [/^Il grafo è incorporato in: (.*)$/u, {it:'Il grafo è incorporato in: $1',en:'The graph is embedded in: $1'}],
    [/^File di log non trovato: (.*)$/u, {it:'File di log non trovato: $1',en:'Log file not found: $1'}]
  ];

  function detectLanguage() {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (SUPPORTED.includes(saved)) return saved;
    const browser = (navigator.language || 'it').toLowerCase().split('-')[0];
    return SUPPORTED.includes(browser) ? browser : 'en';
  }

  async function loadDictionary(lang) {
    if (dictionaries[lang]) return dictionaries[lang];
    const response = await fetch(`/static/i18n/${lang}.json`, {cache:'no-cache'});
    if (!response.ok) throw new Error(`Unable to load ${lang} translations`);
    dictionaries[lang] = await response.json();
    return dictionaries[lang];
  }

  function translate(value) {
    if (typeof value !== 'string' || !value.trim()) return value;
    const dict = dictionaries[current] || {};
    const leading = value.match(/^\s*/)?.[0] || '';
    const trailing = value.match(/\s*$/)?.[0] || '';
    const raw = value.trim();
    if (current === 'it') return value;
    if (Object.prototype.hasOwnProperty.call(dict, raw)) return leading + dict[raw] + trailing;
    for (const [regex, templates] of patterns) {
      const match = raw.match(regex);
      if (match) return leading + templates[current].replace(/\$(\d+)/g, (_, n) => match[Number(n)] || '') + trailing;
    }
    return value;
  }

  function translateElement(root) {
    if (!root) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) translateTextNode(node);
    const elements = root.nodeType === Node.ELEMENT_NODE ? [root, ...root.querySelectorAll('*')] : [...root.querySelectorAll?.('*') || []];
    for (const el of elements) {
      for (const attr of ['title', 'placeholder', 'aria-label']) {
        if (el.hasAttribute?.(attr)) translateAttribute(el, attr);
      }
    }
  }

  function isUserContent(node) {
    const parent = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
    return !!parent?.closest?.('[data-no-translate],input,textarea,code,pre,script,style');
  }

  function translateTextNode(node) {
    if (isUserContent(node)) return;
    const currentValue = node.nodeValue;
    const previousTranslation = lastText.get(node);
    const source = originalText.has(node) && previousTranslation === currentValue
      ? originalText.get(node) : currentValue;
    originalText.set(node, source);
    const next = translate(source);
    lastText.set(node, next);
    if (next !== currentValue) node.nodeValue = next;
  }

  function translateAttribute(el, attr) {
    if (isUserContent(el)) return;
    let attrs = originalAttrs.get(el);
    if (!attrs) { attrs = new Map(); originalAttrs.set(el, attrs); }
    const currentValue = el.getAttribute(attr);
    const record = attrs.get(attr);
    const source = record && record.last === currentValue ? record.source : currentValue;
    const next = translate(source);
    attrs.set(attr, {source, last:next});
    if (next !== currentValue) el.setAttribute(attr, next);
  }

  async function setLanguage(lang, persist = true) {
    if (!SUPPORTED.includes(lang)) lang = 'en';
    try {
      await loadDictionary(lang);
      current = lang;
      document.documentElement.lang = lang;
      if (persist) localStorage.setItem(STORAGE_KEY, lang);
      translateElement(document.body);
      const selector = document.getElementById('languageSelect');
      if (selector) selector.value = lang;
      document.dispatchEvent(new CustomEvent('pixelanim:languagechange', {detail:{language:lang}}));
    } catch (error) {
      console.error('i18n:', error);
    }
  }

  window.t = (key, params = {}) => {
    let value = translate(key);
    for (const [name, replacement] of Object.entries(params)) {
      value = value.replaceAll(`{${name}}`, String(replacement));
    }
    return value;
  };
  window.setAppLanguage = setLanguage;
  window.getAppLanguage = () => current;

  document.addEventListener('DOMContentLoaded', async () => {
    current = detectLanguage();
    await setLanguage(current, false);
    const selector = document.getElementById('languageSelect');
    selector?.addEventListener('change', () => setLanguage(selector.value));
    observer = new MutationObserver(records => {
      for (const record of records) {
        if (record.type === 'characterData') {
          translateTextNode(record.target);
        } else {
          for (const node of record.addedNodes) {
            if (node.nodeType === Node.TEXT_NODE) {
              translateTextNode(node);
            } else if (node.nodeType === Node.ELEMENT_NODE) translateElement(node);
          }
          if (record.type === 'attributes') translateElement(record.target);
        }
      }
    });
    observer.observe(document.body, {subtree:true, childList:true, characterData:true, attributes:true, attributeFilter:['title','placeholder','aria-label']});
  });
})();
