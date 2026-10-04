export const money = (v) => Number(v || 0).toFixed(2).replace(/\.00$/, '')
