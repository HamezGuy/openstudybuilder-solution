<template>
  <section class="workspace-home">
    <h1>{{ $t('Workspace.choose') }}</h1>
    <div class="workspace-grid">
      <router-link
        v-for="item in destinations"
        :key="item.name"
        :to="{ name: item.name }"
        class="workspace-card"
      >
        <v-icon :icon="item.icon" />
        <div>
          <strong>{{ $t(item.title) }}</strong>
          <p>{{ $t(item.hint) }}</p>
        </div>
        <v-icon icon="mdi-arrow-right" size="small" />
      </router-link>
    </div>
    <v-btn variant="text" size="small" @click="showLicense = true">{{
      $t('_global.license')
    }}</v-btn>
    <v-dialog v-model="showLicense" scrollable max-width="800">
      <AboutLicense
        v-if="showLicense"
        :raw-markdown="licenseContent"
        :title="$t('License.title')"
        @close="showLicense = false"
      />
    </v-dialog>
  </section>
</template>
<script setup>
import { computed, ref } from 'vue'
import AboutLicense from '@/components/layout/AboutLicense.vue'
import licenseContent from '../../LICENSE.md?raw'
import { useAccessGuard } from '@/composables/accessGuard'
import roles from '@/constants/roles'
const showLicense = ref(false)
const { checkPermission } = useAccessGuard()
const destinations = computed(() =>
  [
    {
      name: 'SelectOrAddStudy',
      title: 'Workspace.open_study',
      hint: 'Workspace.study_work',
      icon: 'mdi-clipboard-text-outline',
      permission: roles.STUDY_READ,
    },
    {
      name: 'LibrarySummary',
      title: '_global.library',
      hint: 'Workspace.library_work',
      icon: 'mdi-bookshelf',
      permission: roles.LIBRARY_READ,
    },
    {
      name: 'Administration',
      title: 'Workspace.administration',
      hint: 'Workspace.admin_work',
      icon: 'mdi-cog-outline',
      permission: roles.ADMIN_READ,
    },
  ].filter((item) => checkPermission(item.permission))
)
</script>
<style scoped>
.workspace-home {
  max-width: 66rem;
  padding: clamp(24px, 4vw, 56px) 24px;
  margin: auto;
}
h1 {
  font-size: clamp(1.8rem, 4vw, 2.7rem);
  line-height: 1.18;
  margin: 0 0 24px;
}
.workspace-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 230px), 1fr));
  gap: 12px;
  margin-bottom: 24px;
}
.workspace-card {
  display: flex;
  align-items: center;
  gap: 12px;
  background: white;
  color: rgb(var(--v-theme-primary));
  padding: 18px;
  border-radius: 8px;
  text-decoration: none;
}
.workspace-card p {
  font-size: 0.88rem;
  line-height: 1.4;
  color: #3f5064;
  margin: 3px 0 0;
}
.workspace-card strong {
  font-size: 1.12rem;
}
.workspace-card:hover,
.workspace-card:focus-visible {
  outline: 2px solid currentColor;
  outline-offset: 2px;
}
</style>
