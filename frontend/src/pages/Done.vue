<template>
  <div class="wall">
    <h1 class="serif">已完成</h1>
    <article v-for="w in rows" :key="w.id" class="card">
      <h3>{{ w.title }}</h3>
      <p>{{ w.claimer }}</p>
      <!-- 钉的是核销写入时的累计快照，不再随账本变动 -->
      <p class="aside"><ProgressTrack :p="snapshot(w)" variant="snapshot" /></p>
    </article>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
import ProgressTrack from '../components/ProgressTrack.vue'
const rows = ref([])
function snapshot(w) {
  return { ...(w.progress || {}), contributed_snapshot: w.contributed_snapshot }
}
onMounted(async () => { rows.value = await api('/done') })
</script>
