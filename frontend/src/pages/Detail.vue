<template>
  <div class="wall detail">
    <h1 class="serif">{{ w.title }}</h1>
    <p>{{ w.note }}</p>
    <p class="tag">状态 {{ w.status }} · 认领人 {{ w.claimer || '—' }}</p>

    <ProgressTrack v-if="w.progress" :p="w.progress" variant="bar" />

    <!-- 目标：未认领可改，认领后只显示快照，不回刷 -->
    <div v-if="w.status === 'open' || w.status === 'released'" class="row">
      <input v-model="target" type="number" min="0" step="0.01" placeholder="目标金额（留空=无目标）" />
      <button class="ghost" @click="saveTarget">保存目标</button>
    </div>
    <p v-else-if="w.progress && w.progress.target_amount != null" class="tag">
      目标 ¥{{ money(w.progress.target_amount) }}（认领时快照，不随修改回刷）
    </p>

    <p v-if="err" class="err">{{ err }}</p>
    <input v-model="claimer" placeholder="你的名字" />
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button @click="claim">认领锁定</button>
      <button class="ghost" @click="release">释放</button>
      <button class="ghost" @click="fulfill">核销完成</button>
    </div>

    <!-- 凑份子：先预览（不写库），再确认累加 -->
    <section v-if="w.status !== 'fulfilled'" class="chipin">
      <h2 class="serif">凑份子赞助</h2>
      <input v-model="sponsor" placeholder="赞助人名字（可与认领人同名）" />
      <input v-model="amount" type="number" min="0" step="0.01" placeholder="单笔金额（>0）" />
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button @click="previewChip">预览</button>
        <button class="ghost" @click="confirmChip">确认赞助</button>
      </div>
      <p v-if="pv && !pv.confirmed" class="tag">
        预览（未记账，墙角标/进度/我的认领仍为旧累计）：
        累计 ¥{{ money(pv.contributed) }} → ¥{{ money(pv.projected_total) }}，
        缺口 ¥{{ money(pv.gap) }} → ¥{{ money(pv.projected_gap) }}
        <template v-if="pv.target_amount == null">（无目标）</template>
        <template v-else-if="pv.would_reach">· 这笔将凑满目标 ✅</template>
      </p>
      <p v-else-if="pv && pv.confirmed" class="tag">
        已记账：累计 ¥{{ money(pv.contributed) }}，缺口 ¥{{ money(pv.gap) }}
        <template v-if="pv.funded">· 已凑满，可核销 ✅</template>
      </p>
      <ul v-if="w.chip_ins && w.chip_ins.length" class="ledger">
        <li v-for="e in w.chip_ins" :key="e.id">{{ e.sponsor }}：¥{{ money(e.amount) }}</li>
      </ul>
    </section>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
import { money } from '../format'
import ProgressTrack from '../components/ProgressTrack.vue'
const props = defineProps({ id: String })
const w = ref({})
const claimer = ref('访客')
const sponsor = ref('访客')
const amount = ref('')
const target = ref('')
const pv = ref(null)
const err = ref('')
async function load() {
  w.value = await api('/wishes/' + props.id)
  target.value = w.value.target_amount ?? ''
  pv.value = null
}
async function saveTarget() {
  err.value = ''
  try {
    await api('/wishes/' + props.id + '/target', {
      method: 'PATCH',
      body: JSON.stringify({ target_amount: target.value === '' ? null : Number(target.value) }),
    })
    await load()
  } catch (e) { err.value = e.message }
}
async function claim() {
  err.value=''; try { await api('/wishes/'+props.id+'/claim',{method:'POST',body:JSON.stringify({claimer:claimer.value})}); await load() } catch(e){ err.value=e.message }
}
async function release() {
  err.value=''; try { await api('/wishes/'+props.id+'/release',{method:'POST',body:'{}'}); await load() } catch(e){ err.value=e.message }
}
async function fulfill() {
  err.value=''; try { await api('/wishes/'+props.id+'/fulfill',{method:'POST',body:'{}'}); await load() } catch(e){ err.value=e.message }
}
async function callChip(confirm) {
  err.value = ''
  // 确认必须凭一笔未消费的试算：赞助人/金额与试算一致，令牌只能用一次
  if (confirm && (!pv.value || pv.value.confirmed || pv.value.preview_token == null
      || pv.value.sponsor !== sponsor.value.trim()
      || Number(pv.value.amount) !== Number(amount.value))) {
    err.value = '请先按当前赞助人和金额点「预览」，再点「确认赞助」'
    return
  }
  try {
    const r = await api('/wishes/' + props.id + '/chip-in', {
      method: 'POST',
      body: JSON.stringify({
        sponsor: sponsor.value,
        amount: Number(amount.value),
        confirm,
        token: confirm ? pv.value.preview_token : null,
      }),
    })
    if (confirm) {
      // 令牌已消费：清掉输入与试算态，刷新后墙角标/进度/我的认领一起加这笔
      amount.value = ''
      pv.value = null
      await load()
      pv.value = r
    } else {
      // 试算不落库：只显示投影，不刷新页面三处旧累计
      pv.value = r
    }
  } catch (e) { err.value = e.message }
}
const previewChip = () => callChip(false)
const confirmChip = () => callChip(true)
onMounted(load)
</script>
