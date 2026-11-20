<template>
  <div :id="item.id" class="workspace-directory-card" :data-cy="item.title">
    <div class="group-heading">
      <v-icon :icon="item.icon" />
      <h2>{{ item.title }}</h2>
    </div>
    <ul v-if="children.length">
      <li v-for="child in children" :key="child.title">
        <router-link
          v-if="!child.disabled?.()"
          v-slot="{ href }"
          custom
          :to="child.url"
        >
          <a :href="href" @click="navigate($event, child)"
            >{{ child.title }}<v-icon size="small" icon="mdi-chevron-right"
          /></a>
        </router-link>
        <span v-else>{{ child.title }}</span>
      </li>
    </ul>
    <router-link
      v-else-if="item.url && !item.disabled?.()"
      v-slot="{ href }"
      custom
      :to="item.url"
    >
      <a :href="href" @click="navigate($event, null)">{{ item.title }}</a>
    </router-link>
    <details v-if="item.description">
      <summary>{{ $t('Workspace.more') }}</summary>
      <p>{{ item.description }}</p>
    </details>
  </div>
</template>
<script setup>
import { computed } from 'vue'
import { useFeatureFlagsStore } from '@/stores/feature-flags'
const props = defineProps({ item: { type: Object, required: true } })
const emit = defineEmits(['navigate'])
function navigate(event, child) {
  if (
    event.ctrlKey ||
    event.metaKey ||
    event.shiftKey ||
    event.altKey ||
    event.button !== 0
  )
    return
  event.preventDefault()
  emit('navigate', child, props.item)
}
const flags = useFeatureFlagsStore()
const children = computed(() =>
  (props.item.children || []).filter(
    (child) => !child.featureFlag || flags.getFeatureFlag(child.featureFlag)
  )
)
</script>
<style scoped>
.workspace-directory-card {
  height: 100%;
  padding: 14px 16px;
  border: 1px solid #c6d1db;
  background: white;
  border-radius: 7px;
}
.group-heading {
  display: flex;
  align-items: center;
  gap: 9px;
  color: rgb(var(--v-theme-primary));
}
h2 {
  font-size: 1.04rem;
}
ul {
  list-style: none;
  padding: 0;
  margin: 8px 0;
}
li {
  border-top: 1px solid #e7edf1;
}
a {
  color: inherit;
  display: flex;
  justify-content: space-between;
  align-items: center;
  text-decoration: none;
  padding: 5px 2px;
}
a:hover,
a:focus-visible {
  text-decoration: underline;
}
details {
  margin-top: 8px;
  color: #4c5c69;
  font-size: 0.85rem;
}
summary {
  cursor: pointer;
}
</style>
