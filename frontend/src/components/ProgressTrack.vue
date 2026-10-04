<template>
  <!-- 四路同钉：detail 进度条 / wall 墙角标 / mine 旁注 / done 完成时快照 -->
  <span v-if="!p || p.target_amount == null" class="pro pro-none">
    <template v-if="variant === 'badge'">无目标</template>
    <template v-else-if="variant === 'note'">无目标愿望</template>
    <template v-else-if="variant === 'snapshot'">无目标愿望</template>
  </span>

  <span v-else class="pro" :class="'pro-' + variant">
    <span v-if="variant === 'bar'" class="bar"><i :style="{ width: pct + '%' }" :class="{ done: p.funded }"></i></span>
    <span v-if="variant === 'badge'" class="dot" :class="{ done: p.funded }"></span>
    <span class="pro-txt">
      <template v-if="variant === 'badge'">{{ p.funded ? '已满' : pct + '%' }}</template>
      <template v-else-if="variant === 'snapshot'">完成时已筹 ¥{{ money(p.contributed_snapshot ?? p.contributed) }} / ¥{{ money(p.target_amount) }}</template>
      <template v-else>
        ¥{{ money(p.contributed) }} / ¥{{ money(p.target_amount) }} · 缺口 ¥{{ money(p.gap) }}<template v-if="p.funded"> · 已满</template>
      </template>
    </span>
  </span>
</template>
<script setup>
import { computed } from 'vue'
import { money } from '../format'
const props = defineProps({
  p: Object,
  variant: { type: String, default: 'note' }, // bar | badge | note | snapshot
})
const pct = computed(() => Math.round((props.p?.ratio || 0) * 100))
</script>
