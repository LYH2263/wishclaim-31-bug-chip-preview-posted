<template>
  <div class="wall">
    <h1 class="serif">发愿望</h1>
    <input v-model="title" placeholder="标题" />
    <textarea v-model="note" rows="4" placeholder="备注" />
    <input v-model="target" type="number" min="0" step="0.01" placeholder="凑份子目标金额（可留空）" />
    <p class="tag">设了目标的愿望，认领后须凑满金额才能核销</p>
    <button @click="submit">发布</button>
  </div>
</template>
<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
const router = useRouter()
const title = ref('')
const note = ref('')
const target = ref('')
async function submit() {
  const t = target.value === '' ? null : Number(target.value)
  const r = await api('/wishes', { method: 'POST', body: JSON.stringify({ title: title.value, note: note.value, target_amount: t }) })
  router.push('/wishes/' + r.id)
}
</script>
