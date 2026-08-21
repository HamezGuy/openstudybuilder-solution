<template>
  <v-app-bar color="primary" elevation="1" height="70">
    <v-app-bar-nav-icon
      v-if="!props.hideAppBarNavIcon"
      data-cy="topbar-menu-button"
      elevation="4"
      @click="appStore.drawer = !appStore.drawer"
    />
    <div data-cy="topbar-logo" class="d-flex action" @click="navigateToRoot">
      <v-img
        class="mx-6"
        :src="sbLogoUrl"
        contain
        transition="scale-transition"
        width="190"
      />
      <div
        v-if="appEnv"
        class="mr-6 font-weight-black"
        style="font-size: xx-large"
      >
        {{ appEnv }}
      </div>
    </div>

    <v-toolbar-items class="hidden-xs-only">
      <v-btn
        v-for="app in availableApps"
        :key="app.name"
        :data-cy="app.name"
        class="text-capitalize"
        :to="{ name: app.name }"
        variant="text"
      >
        <v-icon class="mr-1" :icon="app.icon" />
        {{ app.name }}
      </v-btn>
      <v-btn
        v-if="isAuthenticated && checkPermission(roles.ADMIN_READ)"
        class="text-capitalize"
        href="/neodash/"
        target="_blank"
        variant="text"
      >
        <v-icon icon="mdi-file-chart-outline" />
        {{ $t('_global.reports') }}
        <template #append>
          <v-icon icon="mdi-open-in-new" />
        </template>
      </v-btn>
    </v-toolbar-items>
    <v-spacer />
    <div v-if="isAuthenticated">
      <v-btn
        variant="flat"
        rounded
        class="text-label-large"
        :color="
          currentStudyStatus === 'DRAFT'
            ? 'green'
            : currentStudyStatus === 'LOCKED'
              ? 'red'
              : 'info'
        "
        @click="openSelectStudyDialog"
        @mouseenter="isHovered = true"
        @mouseleave="isHovered = false"
      >
        <span v-if="isHovered || !selectedStudy">{{
          $t('Topbar.select_study')
        }}</span>
        <span v-else>
          {{ selectedStudyId }}
          {{ selectedStudyVersion ? 'v' + selectedStudyVersion : '' }}
        </span>
        <v-icon
          v-if="isHovered || !selectedStudy"
          size="large"
          location="right"
          icon="mdi-menu-down"
        />
        <v-icon
          v-else
          location="right"
          size="small"
          class="ml-1 mr-1"
          :icon="
            currentStudyStatus === 'DRAFT'
              ? 'mdi-lock-open-outline'
              : 'mdi-lock-outline'
          "
        />
      </v-btn>
    </div>
    <v-btn
      v-if="isAuthenticated"
      data-cy="topbar-admin-icon"
      icon="mdi-cog-outline"
      :title="$t('_global.preferences')"
      @click="preferencesDialog = true"
    />
    <v-menu location="bottom">
      <template #activator="{ props }">
        <v-btn
          data-cy="topbar-help"
          icon="mdi-help-circle-outline"
          :title="$t('Topbar.help')"
          v-bind="props"
        />
      </template>
      <v-list density="compact">
        <v-list-item
          data-cy="topbar-api"
          :href="$config.API_BASE_URL"
          target="_blank"
        >
          <template #prepend>
            <v-icon>mdi-script-text</v-icon>
          </template>
          <v-list-item-title>{{ $t('Topbar.api') }}</v-list-item-title>
        </v-list-item>
        <v-list-item
          data-cy="topbar-documentation-portal"
          :href="documentationPortalUrl"
          target="_blank"
        >
          <template #prepend>
            <v-icon>mdi-book-open-outline</v-icon>
          </template>
          <v-list-item-title>{{
            $t('Topbar.documentation_portal')
          }}</v-list-item-title>
        </v-list-item>
        <v-list-item
          data-cy="topbar-user-guide"
          :href="appStore.helpUrl"
          target="_blank"
        >
          <template #prepend>
            <v-icon>mdi-book-open-outline</v-icon>
          </template>
          <v-list-item-title>{{ $t('Topbar.user_guide') }}</v-list-item-title>
        </v-list-item>
        <v-list-item
          data-cy="topbar-need-help"
          :href="$config.NEED_HELP_URL"
          target="_blank"
        >
          <template #prepend>
            <v-icon>mdi-book-open-outline</v-icon>
          </template>
          <v-list-item-title>{{ $t('Topbar.need_help') }}</v-list-item-title>
        </v-list-item>
        <v-list-item data-cy="topbar-about" @click="openAboutBox">
          <template #prepend>
            <v-icon>mdi-information-outline</v-icon>
          </template>
          <v-list-item-title>{{ $t('Topbar.about') }}</v-list-item-title>
        </v-list-item>
      </v-list>
    </v-menu>
    <v-menu v-if="isAuthenticated" offset-y>
      <template #activator="{ props }">
        <v-btn
          id="user-menu-btn"
          data-cy="topbar-user-name"
          class="ma-2 text-white"
          variant="text"
          v-bind="props"
        >
          <v-icon right class="mx-2"> mdi-account-outline </v-icon>
          {{ username }}
        </v-btn>
      </template>
      <v-list density="compact">
        <v-list-item v-if="authStore.userInfo">
          <template #prepend>
            <v-icon>mdi-security</v-icon>
          </template>
          <v-list-item-title>{{
            $t('_global.access_groups')
          }}</v-list-item-title>
          <v-list-item-subtitle
            v-for="(role, index) of authStore.userInfo.roles"
            :key="index"
          >
            {{ role }}
          </v-list-item-subtitle>
        </v-list-item>
        <v-list-item :to="{ name: 'Logout' }" data-cy="topbar-logout">
          <template #prepend>
            <v-icon>mdi-export</v-icon>
          </template>
          <v-list-item-title>{{ $t('_global.logout') }}</v-list-item-title>
        </v-list-item>
      </v-list>
    </v-menu>
    <v-btn
      v-else
      id="login-btn"
      data-cy="topbar-login"
      class="text-capitalize mr-4"
      :to="{ name: 'Login' }"
      variant="text"
    >
      <v-icon>mdi-login</v-icon>
      {{ $t('_global.login') }}
    </v-btn>
    <div class="d-flex">
      <v-img
        class="mr-4"
        :src="nnLogoUrl"
        contain
        transition="scale-transition"
        width="50"
        height="50"
      />
    </div>
    <v-dialog v-model="showAboutDialog" fullscreen>
      <AboutPage @close="showAboutDialog = false" />
    </v-dialog>
    <ConfirmDialog ref="confirm" :text-cols="5" :action-cols="6">
      <template #actions>
        <v-btn
          color="nnBaseBlue"
          rounded="xl"
          elevation="1"
          @click="openSelectStudyDialog"
        >
          {{ $t('_global.select_study') }}
        </v-btn>
        <v-btn
          v-if="checkPermission(roles.STUDY_WRITE)"
          color="nnBaseBlue"
          rounded="xl"
          elevation="1"
          @click="redirectToStudyTable"
        >
          {{ $t('_global.add_study') }}
        </v-btn>
      </template>
    </ConfirmDialog>
    <v-dialog v-model="showSelectForm" persistent max-width="600px" z-index="4000">
      <StudyQuickSelectForm
        @close="showSelectForm = false"
        @selected="reloadPage"
      />
    </v-dialog>
    <v-dialog
      v-model="preferencesDialog"
      max-width="650"
      @keydown.esc="preferencesDialog = false"
    >
      <UserPreferences @close="preferencesDialog = false" />
    </v-dialog>
  </v-app-bar>
</template>

<script setup>
import { computed, inject, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { useAuthStore } from '@/stores/auth'
import { useAccessGuard } from '@/composables/accessGuard'
import { useStudiesGeneralStore } from '@/stores/studies-general'
import AboutPage from '@/components/layout/AboutPage.vue'
import ConfirmDialog from '@/components/tools/ConfirmDialog.vue'
import StudyQuickSelectForm from '@/components/studies/StudyQuickSelectForm.vue'
import { getAppEnv } from '@/utils/generalUtils'
import UserPreferences from '@/views/user/UserPreferences.vue'

const props = defineProps({
  hideAppBarNavIcon: {
    type: Boolean,
    default: false,
  },
})
const router = useRouter()
const emit = defineEmits(['backToRoot'])
const roles = inject('roles')
const $config = inject('$config')
const isHovered = ref(false)

const appStore = useAppStore()
const authStore = useAuthStore()
const studiesGeneralStore = useStudiesGeneralStore()
const { checkPermission } = useAccessGuard()
const sbLogoUrl = new URL(
  '../../assets/study_builder_homepage_logo.png',
  import.meta.url
).href
const nnLogoUrl = new URL(
  '../../assets/nn_logo_rgb_white_small.png',
  import.meta.url
).href

const appEnv = getAppEnv()

const selectedStudy = computed(() => studiesGeneralStore.selectedStudy)
const selectedStudyId = computed(() => studiesGeneralStore.studyId)
const selectedStudyVersion = computed(() => studiesGeneralStore.studyVersion)
const currentStudyStatus = computed(() => studiesGeneralStore.studyStatus)

const apps = [
  {
    icon: 'mdi-stethoscope',
    name: 'Studies',
    needsAuthentication: true,
  },
    {
    icon: 'mdi-bookshelf',
    name: 'Library',
    needsAuthentication: true,
    requiredRole: roles.LIBRARY_READ,
  },
  {
    icon: 'mdi-wrench-outline',
    name: 'Administration',
    needsAuthentication: true,
    requiredRole: roles.ADMIN_WRITE,
  },
]

const showAboutDialog = ref(false)
const preferencesDialog = ref(false)
const showSelectForm = ref(false)
const confirm = ref()

const documentationPortalUrl = computed(() => {
  return $config.DOC_BASE_URL
})
const username = computed(() => {
  if (!authStore.identityResolved) return ''
  return (
    authStore.userInfo?.name ||
    authStore.userInfo?.username ||
    authStore.userInfo?.preferred_username ||
    ''
  )
})
const isAuthenticated = computed(() => {
  if (!authStore.identityResolved) return false
  if (authStore.userInfo) return true
  return !$config.OAUTH_ENABLED
})
const availableApps = computed(() => {
  if (!authStore.identityResolved) return []
  return apps.filter(
    (app) =>
      !app.needsAuthentication ||
      (isAuthenticated.value &&
        (!app.requiredRole || checkPermission(app.requiredRole)))
  )
})

function navigateToRoot() {
  emit('backToRoot')
}
function openAboutBox() {
  showAboutDialog.value = true
}
function openSelectStudyDialog() {
  showSelectForm.value = true
}
function redirectToStudyTable() {
  confirm.value.cancel()
  router.push({ name: 'SelectOrAddStudy' })
}
function reloadPage() {
  const uid = selectedStudy.value?.uid
  if (!uid) return
  const current = router.currentRoute.value
  if (current.params.study_id && current.params.study_id !== uid) {
    router.replace({
      name: current.name,
      params: { ...current.params, study_id: uid },
      query: current.query,
    })
  }
}
</script>

<style scoped lang="scss">
@use 'vuetify/settings';
.action {
  cursor: pointer;
}
.text-capitalize {
  text-transform: capitalize !important;
}
.v-toolbar {
  &-items {
    align-items: center;
    .v-btn {
      height: 60% !important;
      &--active {
        background-color: rgb(var(--v-theme-secondary));
        border-radius: settings.$border-radius-root * 2 !important;

        &::before {
          opacity: 0;
        }
      }
      &:hover {
        border-radius: settings.$border-radius-root * 2 !important;
      }
    }
  }
}
</style>
