"""Expose selected native product content without authoring missing drug facts."""

from usdm_model.administrable_product import AdministrableProduct
from usdm_model.alias_code import AliasCode
from usdm_model.code import Code
from usdm_model.ingredient import Ingredient
from usdm_model.strength import Strength
from usdm_model.substance import Substance

from clinical_mdr_api.services.ddf.usdm_mapping_context import (
    USDMMappingAuthorityRequired,
    native_json,
)


class NativeInterventionMapping:
    def __init__(self, mapper):
        self.mapper = mapper
        self.context = mapper._context
        self._products = {}
        self._product_sources = {}

    def identifier(self, kind, source):
        return self.mapper._id_manager.get_id(kind, source)

    def extension(self, kind, uid, source):
        return self.mapper._native_extension(kind, uid, source)

    def issue(self, code, source, target, message, resolution):
        self.context.unresolved(code, source, target, message, resolution)

    def apply(self, version):
        interventions = {row.id: row for row in version.studyInterventions}
        selections = {
            row.study_compound_uid: row for row in self.mapper._study_compounds
        }
        for dosing in self.mapper._study_compound_dosings:
            selected_uid = getattr(dosing.study_compound, "study_compound_uid", None)
            if selected_uid not in selections:
                self.issue(
                    "USDM_DOSING_COMPOUND_UNRESOLVED",
                    f"study-compound-dosings/{dosing.study_compound_dosing_uid}/study_compound",
                    "StudyIntervention/administrations",
                    "The dosing record names no compound selection in this exact study snapshot.",
                    "Resolve the native compound selection; the dosing record remains source evidence.",
                )
        for uid, selection in selections.items():
            intervention = interventions.get(self.identifier("StudyIntervention", uid))
            bindings = getattr(selection, "native_library_bindings", [])
            sources = getattr(selection, "pharmaceutical_products", [])
            by_uid = {}
            for product in sources:
                if not product.uid or not product.version:
                    self.issue(
                        "USDM_PRODUCT_SOURCE_VERSION_REQUIRED",
                        f"study-compounds/{uid}/pharmaceutical_products",
                        "StudyVersion/administrableProducts",
                        "A full native product requires its exact UID and selected version.",
                        "Read the selected native product value; do not substitute the latest library version.",
                    )
                    continue
                key = product.uid, product.version
                source = native_json(product)
                if key in self._product_sources and self._product_sources[key] != source:
                    raise USDMMappingAuthorityRequired(
                        f"USDM_PRODUCT_SOURCE_CONFLICT: {product.uid}@{product.version}"
                    )
                if product.uid in by_uid and by_uid[product.uid] != key:
                    raise USDMMappingAuthorityRequired(
                        f"USDM_PRODUCT_SELECTION_AMBIGUOUS: {uid}/{product.uid}"
                    )
                by_uid[product.uid] = key
                if key not in self._products:
                    self._product_sources[key] = source
                    self._products[key] = self._product(product, bindings)
                    self.context.retain(
                        "pharmaceuticalProductDefinition", f"{product.uid}@{product.version}",
                        product,
                        scope={
                            "studyUid": self.mapper._study_uid,
                            "studyValueVersion": self.mapper._study_value_version,
                        },
                    )
            # A compact catalog reference cannot stand in for formulations,
            # ingredients, quantities or independently versioned children.
            compact = getattr(getattr(selection, "medicinal_product", None),
                              "pharmaceutical_products", [])
            for reference in compact:
                if reference.uid not in by_uid:
                    self.issue(
                        "USDM_PRODUCT_DEFINITION_UNRESOLVED",
                        f"study-compounds/{uid}/medicinal_product/pharmaceutical_products/{reference.uid}",
                        "StudyVersion/administrableProducts",
                        "Only a compact native product reference is present.",
                        "Read its exact scoped definition, including formulation and ingredient dependencies.",
                    )
            if intervention is None:
                continue
            intervention.extensionAttributes.append(
                self.extension("studyCompound", uid, selection)
            )
            selected = set()
            unresolved_selection = False
            for binding in bindings:
                if binding.get("kind") != "pharmaceuticalProduct" or binding.get("mode") != "selected-value":
                    continue
                if (
                    binding.get("studyUid") != self.mapper._study_uid
                    or binding.get("studyValueVersion") != self.mapper._study_value_version
                    or binding.get("studyCompoundUid") != uid
                ):
                    raise USDMMappingAuthorityRequired("USDM_PRODUCT_SELECTION_SCOPE_MISMATCH")
                key = binding.get("uid"), binding.get("version")
                if key not in self._products or by_uid.get(key[0]) != key:
                    unresolved_selection = True
                    self.issue(
                        "USDM_PRODUCT_SELECTED_DEFINITION_MISSING",
                        f"study-compounds/{uid}/native_library_bindings",
                        "Administration/administrableProductId",
                        "The directly selected product value has no matching full definition.",
                        "Resolve the exact selected product UID and version before assigning it.",
                    )
                    continue
                selected.add(key)
            if intervention.administrations:
                if len(selected) == 1 and not unresolved_selection:
                    product_id = self._products[next(iter(selected))].id
                    for administration in intervention.administrations:
                        administration.administrableProductId = product_id
                elif sources or compact or selected or unresolved_selection:
                    self.issue(
                        "USDM_ADMINISTRABLE_PRODUCT_ASSIGNMENT_UNRESOLVED",
                        f"study-compounds/{uid}/pharmaceutical_products",
                        "Administration/administrableProductId",
                        "The native dosing relationship does not identify one selected administrable product.",
                        "Review the product for each administration; catalog order, name and allowed route are not selection authority.",
                    )
        version.administrableProducts = list(self._products.values())

    def _product(self, product, bindings):
        uid = f"{product.uid}@{product.version}"
        source_path = f"pharmaceutical-products/{uid}"
        dose_form = None
        forms = product.dosage_forms or []
        if len(forms) == 1:
            dose_form = AliasCode(
                id=self.identifier("AliasCode", "product-dose-form:" + uid),
                standardCode=self.mapper.get_ct_package_term_as_usdm_code(forms[0].term_uid),
            )
        elif len(forms) > 1:
            self.issue(
                "USDM_PRODUCT_DOSE_FORM_AMBIGUOUS",
                source_path + "/dosage_forms", "AdministrableProduct/administrableDoseForm",
                "The native product has multiple dose forms; USDM requires one.",
                "Resolve an explicit administrable product or reviewed dose form. Every native form remains in typed metadata.",
            )
        ingredients = []
        for formulation_index, formulation in enumerate(product.formulations):
            for ingredient_index, ingredient in enumerate(formulation.ingredients):
                path = f"{source_path}/formulations/{formulation_index}/ingredients/{ingredient_index}"
                key = f"{uid}:formulation:{formulation_index}:ingredient:{ingredient_index}"
                ingredients.append(self._ingredient(ingredient, bindings, key, path))
        return self.context.build(
            AdministrableProduct, source_path,
            id=self.identifier("AdministrableProduct", uid),
            name=product.derived_name or product.external_id or product.uid,
            label=product.derived_name,
            administrableDoseForm=dose_form,
            # OSB's product and ingredient models do not state the required
            # USDM designation or ingredient role. Preview keeps those absent.
            ingredients=ingredients,
            extensionAttributes=[self.extension("pharmaceuticalProduct", uid, product)],
        )

    def _ingredient(self, ingredient, bindings, key, source_path):
        source = ingredient.active_substance
        strengths = []
        if ingredient.strength is not None:
            quantity = self.mapper._native_dose_quantity(
                ingredient.strength, "ingredient-strength:" + key,
                native_bindings=bindings,
            )
            strengths.append(self.context.build(
                Strength, source_path + "/strength",
                id=self.identifier("Strength", key), name=ingredient.strength.uid,
                numerator=quantity,
                extensionAttributes=[
                    self.extension("ingredient-strength", key, ingredient.strength)
                ],
            ))
        unii = source.unii
        codes = []
        if unii is not None:
            readings = [
                row for row in bindings
                if row.get("kind") == "dictionarySubstance"
                and row.get("uid") == unii.substance_term_uid
            ]
            if len(readings) == 1:
                reading = readings[0]
                codes.append(self.context.build(
                    Code, source_path + "/active_substance/unii",
                    id=self.identifier("Code", "substance-unii:" + key),
                    code=unii.substance_unii, decode=unii.substance_name,
                    codeSystem=reading.get("libraryName"),
                    codeSystemVersion=reading.get("version"),
                ))
            else:
                self.issue(
                    "USDM_SUBSTANCE_DICTIONARY_VERSION_REQUIRED",
                    source_path + "/active_substance/unii", "Substance/codes",
                    "The substance code lacks one exact dictionary version reading.",
                    "Resolve the selected substance dictionary value; its native metadata remains intact.",
                )
        substance = self.context.build(
            Substance, source_path + "/active_substance",
            id=self.identifier("Substance", key),
            name=(source.inn or (unii.substance_name if unii else None)
                  or source.long_number or source.short_number
                  or source.analyte_number or source.uid),
            strengths=strengths, codes=codes,
            extensionAttributes=[self.extension("activeSubstance", key, source)],
        )
        return self.context.build(
            Ingredient, source_path, id=self.identifier("Ingredient", key),
            substance=substance,
            extensionAttributes=[self.extension("pharmaceuticalIngredient", key, ingredient)],
        )
