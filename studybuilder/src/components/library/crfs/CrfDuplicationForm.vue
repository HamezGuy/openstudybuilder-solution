<template>
  <v-card>
    <v-card-title>
      {{ $t('CRFDuplicationForm.duplicate') }}
    </v-card-title>
    <v-card-text>
      <v-form ref="observer">
        <v-row>
          <v-col v-if="type !== crfTypes.ITEM" cols="2">
            <v-checkbox
              v-model="relations"
              :label="$t('CRFDuplicationForm.include')"
              class="mt-6 ml-2"
            />
          </v-col>
          <v-col v-if="type !== crfTypes.ITEM" cols="10">
            <OdmReferencesTree
              :item="item"
              :type="type"
              no-title
              no-actions
              :full-data="false"
              :open-all="relations"
            />
          </v-col>
        </v-row>
        <v-row>
          <div class="ml-4">
            {{ $t('CRFDuplicationForm.attributes') }}
          </div>
        </v-row>
        <v-row>
          <v-col cols="4">
            <v-text-field
              v-model="name"
              :label="$t('_global.name')"
              clearable
              :rules="[formRules.required]"
            />
          </v-col>
          <v-col cols="4">
            <v-text-field v-model="oid" :label="$t('_global.oid')" clearable />
          </v-col>
          <v-col cols="4">
            <v-autocomplete
              v-if="type !== crfTypes.COLLECTION"
              v-model="itemToLinkTo"
              :items="itemsToLinkTo"
              :label="$t('CRFDuplicationForm.item_to_link')"
              item-title="name"
              item-value="uid"
              clearable
              :rules="[formRules.required]"
              return-object
            />
          </v-col>
        </v-row>
      </v-form>
    </v-card-text>
    <v-card-actions>
      <v-spacer />
      <v-btn class="primary" @click="save()">
        {{ $t('_global.save') }}
      </v-btn>
      <v-btn class="secondary-btn" color="white" @click="close()">
        {{ $t('_global.close') }}
      </v-btn>
    </v-card-actions>
  </v-card>
</template>

<script setup>
import { inject, onMounted, ref, watch } from 'vue'

import OdmReferencesTree from '@/components/library/crfs/OdmReferencesTree.vue'
import crfs from '@/api/crfs'
import crfTypes from '@/constants/crfTypes'

const props = defineProps({
  open: Boolean,
  item: {
    type: Object,
    default: null,
  },
  type: {
    type: String,
    default: null,
  },
})

const emit = defineEmits(['close'])

const formRules = inject('formRules')

const observer = ref(null)

const relations = ref(false)
const name = ref('')
const oid = ref('')
const form = ref({})
const itemsToLinkTo = ref([])
const itemToLinkTo = ref(null)

const getElementsToLinkTo = () => {
  if (props.type === crfTypes.FORM) {
    crfs.get('study-events', {}).then((resp) => {
      itemsToLinkTo.value = resp.data.items
    })
  } else if (props.type === crfTypes.GROUP) {
    crfs.getCrfForms().then((resp) => {
      itemsToLinkTo.value = resp.data
    })
  } else if (props.type === crfTypes.ITEM) {
    crfs.getCrfGroups().then((resp) => {
      itemsToLinkTo.value = resp.data
    })
  }
}

watch(
  () => props.type,
  () => {
    getElementsToLinkTo()
  }
)

onMounted(() => {
  getElementsToLinkTo()
})

const close = () => {
  emit('close')
}

const save = async () => {
  const { valid } = await observer.value.validate()
  if (!valid) return

  let resp

  form.value = Object.assign(form.value, props.item)
  form.value.name = name.value
  form.value.oid = oid.value

  if (props.type === crfTypes.COLLECTION) {
    resp = await crfs.createCollection(form.value)
    if (relations.value) {
      await crfs.addFormsToCollection(props.item.forms, resp.data.uid, true)
    }
    close()
  } else if (props.type === crfTypes.FORM) {
    form.value.alias_uids = form.value.aliases.map((alias) => alias.uid)
    resp = await crfs.createForm(form.value)
    form.value.uid = resp.uid
    if (relations.value) {
      crfs.addItemGroupsToForm(props.item.item_groups, resp.data.uid, true)
    }
    await crfs.addFormsToCollection([form.value], itemToLinkTo.value.uid, false)
    close()
  } else if (props.type === crfTypes.GROUP) {
    form.value.alias_uids = form.value.aliases.map((alias) => alias.uid)
    form.value.sdtm_domain_uids = form.value.sdtm_domains.map(
      (sdtm) => sdtm.uid
    )
    resp = await crfs.createItemGroup(form.value)
    form.value.uid = resp.uid
    if (relations.value) {
      crfs.addItemsToItemGroup(props.item.items, resp.data.uid, true)
    }
    await crfs.addItemGroupsToForm([form.value], itemToLinkTo.value.uid, false)
    close()
  } else {
    form.value.alias_uids = form.value.aliases.map((alias) => alias.uid)
    if (form.value.codelist) {
      form.value.codelist_uid = form.value.codelist.uid
    }
    if (form.value.unit_definitions) {
      for (const unit of form.value.unit_definitions) {
        form.value.unit_definitions[
          form.value.unit_definitions.indexOf(unit)
        ].mandatory = unit.mandatory !== null && unit.mandatory !== false
      }
    }
    form.value.terms.forEach((term) => {
      term.uid = term.term_uid
      delete term.term_uid
    })
    resp = await crfs.createItem(form.value)
    form.value.uid = resp.uid
    crfs.addItemsToItemGroup([form.value], itemToLinkTo.value.uid, false)
    close()
  }
}
</script>
