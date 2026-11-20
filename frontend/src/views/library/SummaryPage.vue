<template>
  <section class="workflow-directory">
    <h1 class="page-title">{{ $t('_global.library') }}</h1>
    <div data-cy="tiles-box" class="directory-grid">
      <ExpandableHeaderContent
        v-for="item in appStore.libraryMenu.items.slice(1)"
        :key="item.id || item.title"
        :item="item"
        @navigate="navigate"
      />
    </div>
    <RedirectHandler :target="target" />
  </section>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import ExpandableHeaderContent from '@/components/tools/ExpandableHeaderContent.vue'
import RedirectHandler from '@/components/tools/RedirectHandler.vue'
import { useAppStore } from '@/stores/app'
const appStore = useAppStore()
const target = ref({})
function navigate(subitem, item) {
  target.value = { subitem, item }
}
onMounted(() => appStore.fetchCtLibraries())
</script>
<style scoped>
.directory-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 245px), 1fr));
  gap: 12px;
  align-items: start;
}
</style>
