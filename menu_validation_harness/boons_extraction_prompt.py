"""Boons / n8n OUTPUT CONTRACT for menu extraction (modifiers + nested submodifiers).

Used in POC mode so Dynamo `processing_result` matches the review UI schema.
Base+surcharge math is applied in Python (`menu_tools`) — extract printed absolute prices.

Prompt shape follows AWS Sonnet guidance (compact XML tags, one positive
example, absolute prices).
"""

# Vision / scanned PDF / image — AWS-style compact contract (Sonnet 4.6).
BOONS_EXTRACTION_PROMPT = """\
You are a menu data extraction system. The attached restaurant menu is provided as a PDF or image.
Extract ALL visible items from ALL pages/locations and return ONLY a valid JSON array (no markdown, no commentary).

<multi_location>
If the document contains menus from multiple locations, extract everything.
Same item + same price across locations → extract once. Same item + different price → extract both.
Do not stop at the first address or phone number.
</multi_location>

<output_format>
JSON array of category objects. Follow this example precisely:
[
  {
    "category": "APPETIZERS",
    "items": [
      {
        "name": "Spring Rolls",
        "price": 8.99,
        "description": "Crispy vegetable rolls with sweet chili sauce",
        "dietary_tags": "Vegetarian",
        "modifiers": []
      },
      {
        "name": "Chicken Wings",
        "price": 10.0,
        "description": "",
        "dietary_tags": "",
        "modifiers": [
          {
            "title": "Size",
            "item_limit": "1",
            "mandatory": "yes",
            "addonqty": "1",
            "type": "radio",
            "option": {
              "option_id": ["", ""],
              "name": ["6pc", "12pc"],
              "price": ["10", "16"],
              "unit": ["", ""]
            },
            "options_detail": [
              {"name": "6pc", "price": "10", "unit": "", "submodifier": null},
              {"name": "12pc", "price": "16", "unit": "", "submodifier": null}
            ]
          }
        ]
      },
      {
        "name": "Nachos",
        "price": 12.99,
        "description": "Tortilla chips with melted cheese",
        "dietary_tags": "Vegetarian, Gluten-Free",
        "modifiers": [
          {
            "title": "Add-ons",
            "item_limit": "0",
            "mandatory": "no",
            "addonqty": "0",
            "type": "checkbox",
            "option": {
              "option_id": ["", ""],
              "name": ["Guacamole", "Sour Cream"],
              "price": ["2.5", "1.5"],
              "unit": ["", ""]
            },
            "options_detail": [
              {"name": "Guacamole", "price": "2.5", "unit": "", "submodifier": null},
              {"name": "Sour Cream", "price": "1.5", "unit": "", "submodifier": null}
            ]
          }
        ]
      },
      {
        "name": "Paneer Tikka",
        "price": 14.99,
        "description": "Grilled cottage cheese with spices",
        "dietary_tags": "Vegetarian",
        "modifiers": [
          {
            "title": "Spice Level",
            "item_limit": "1",
            "mandatory": "yes",
            "addonqty": "1",
            "type": "radio",
            "option": {
              "option_id": ["", "", ""],
              "name": ["Mild", "Medium", "Hot"],
              "price": ["", "", ""],
              "unit": ["", "", ""]
            },
            "options_detail": [
              {
                "name": "Mild", "price": "", "unit": "",
                "submodifier": {
                  "title": "Mild", "item_limit": "1", "mandatory": "yes", "addonqty": "1", "type": "radio",
                  "option": {"option_id": ["", ""], "name": ["Low Spice", "No Chili"], "price": ["", ""], "unit": ["", ""]},
                  "options_detail": [
                    {"name": "Low Spice", "price": "", "unit": "", "submodifier": null},
                    {"name": "No Chili", "price": "", "unit": "", "submodifier": null}
                  ]
                }
              },
              {
                "name": "Medium", "price": "", "unit": "",
                "submodifier": {
                  "title": "Medium", "item_limit": "1", "mandatory": "yes", "addonqty": "1", "type": "radio",
                  "option": {"option_id": ["", ""], "name": ["Regular Spice", "Extra Masala"], "price": ["0.5", "0.75"], "unit": ["", ""]},
                  "options_detail": [
                    {"name": "Regular Spice", "price": "0.5", "unit": "", "submodifier": null},
                    {"name": "Extra Masala", "price": "0.75", "unit": "", "submodifier": null}
                  ]
                }
              },
              {
                "name": "Hot", "price": "", "unit": "",
                "submodifier": {
                  "title": "Hot", "item_limit": "1", "mandatory": "yes", "addonqty": "1", "type": "radio",
                  "option": {"option_id": ["", ""], "name": ["Spicy", "Extra Hot"], "price": ["1", "1.5"], "unit": ["", ""]},
                  "options_detail": [
                    {"name": "Spicy", "price": "1", "unit": "", "submodifier": null},
                    {"name": "Extra Hot", "price": "1.5", "unit": "", "submodifier": null}
                  ]
                }
              }
            ]
          }
        ]
      }
    ]
  }
]
</output_format>

<rules>
<schema>
Every item MUST have all 5 fields: name (string), price (number), description (string, "" if none), dietary_tags (comma-separated string, "" if none), modifiers (array, [] if none). Never omit any field.
Do not add top-level addons or options on an item — every choice group goes in modifiers only.
</schema>

<prices>
item.price is always a NUMBER (the printed dish price). Use 0.0 only if truly free.
Modifier option prices are STRINGS with the ABSOLUTE printed price ("10", "16"). Use "" if included in base or no extra charge.
Post-processing handles surcharge math — extract what is printed. Do not subtract.
If a mandatory size/portion group has prices and no standalone dish price, set item.price to the cheapest printed size (still a number); leave option prices as those printed absolutes.
</prices>

<modifier_triggers>
Create a modifier group when the menu shows:
- Size/portion options (Small/Medium/Large, Half/Full, 6pc/12pc)
- Protein/flavor/crust choices
- Add-on extras with price (Extra Cheese $2, Add Pav $1.5)
- Spice levels (Mild/Medium/Hot)
- Bracketed choice list: "Item(OptionA, OptionB)" → name=Item, choices become a modifier
- Inline "Add on X $N" / "Add X $N" / "extra X $N" (including after the price in parentheses) → Add-ons checkbox; trim leading Add on/Add/Extra/With from the option name
- A priced indented line under an item
- "choice of A, or B" in the name or under the dish → keep that parenthetical in the name (Python builds required Choose one)
- "Build Your Own" + "up to 3" + a printed meat list → checkbox of every meat (keep Vietnamese in option names), item_limit 3; do not leave the meat list in the description
- "sunny side up egg as requested" in a category blurb → optional Add-ons on every item in that category; strip the phrase from descriptions
- "(with your choice of protein)" + shared plate sentence → Choice of Protein checkbox of those ingredients on every item in the category
- Extra Meat "Rare Beef or Tripe $5" → two checkbox options at the same printed price
</modifier_triggers>

<modifier_types>
- radio: type="radio", mandatory="yes", item_limit="1", addonqty="1" — choose exactly one (sizes, proteins)
- checkbox: type="checkbox", mandatory="no", addonqty="0" — optional add-ons
- item_limit for checkbox = the printed MAX selectable ("up to 3" → "3"), not option count. "0" if unlimited.
</modifier_types>

<nesting>
When headings (Mild/Medium/Hot, Regular/Large) have sub-choices underneath:
- Parent option.name = headings ONLY
- Sub-choices go inside options_detail[i].submodifier (full modifier object)
- Leaf options use submodifier: null
Use flat (all submodifier: null) only when no sub-levels exist.
</nesting>

<modifier_association>
Attach a modifier only to the item directly above it until the next item name.
Copy the same group to multiple items only when:
- the menu splits "X / Y" with one shared price table below, or
- a category-level CHOICE OF MEAT banner applies to every priced dish in that category, or
- an extras section is for another dish type (Extra Meat for Pho → every Pho item).
A slash-protein list printed in one dish name (Chicken/Beef/Veggie/Shrimp (+$N)) stays on that dish only.
If association is ambiguous → modifiers: [].
Margin/sidebar/footer text that applies to all items → do not attach as a modifier to any single item.
</modifier_association>

<item_splitting>
- "A / B" with same price, no per-variant pricing → split into separate items; copy the shared modifier onto each (Dahi Bhalla/Dahi Vada)
- "A / B" with shared price table below → split items, copy the modifier to each
- "Item 6pc/12pc" with dual prices "10/16" → one item, Size radio, absolute option prices
- "Item / (5 oz) $A / (10 oz) $B" (second size may wrap) → one item, Size radio, absolute option prices
- Multi-column sizes (S/M/L or oz headers with a price per column) → one item, Size radio, every column's absolute price (keep cents)
- "A/B/C/D (+$N)" → one item, Choose-one with every slash option; only the last option gets +N, earlier options ""
- Combo "1 Bajra/1 Jowar/2 Roti (CHOOSE ONE) & 8 oz Ker Sangri Sabzi" → name="8 oz Ker Sangri Sabzi Combo"; bread is a required Choose one; do not put that printed line in the description. Same price 16 oz Papad/Gatte line stays one item with Choose Sabzi. Do not split DRY/GRAVY. Strip decorative * / ✲ and leading & from names.
- Leader dots / wrap: a $ on a continuation line belongs to THIS name, never the next dish
- Mid-line $A and right-margin $B on the same name line are BOTH this item (taco $3.95 / carne asada $4.95 → base 3.95 and a Carne Asada option; the row below keeps its own $)
- "Choice of: … $P / $Q" lines under one name are that item's protein tiers only — do not put them on the following dish
- Per-item extras stay on that item (Tostada add $2 proteins do not go on Two Flautas)
</item_splitting>

<parenthetical_choices>
"Can soda(Limca, pepsi, coke/diet)" → name="Can soda", mandatory radio title="Choose one", options split on commas. Keep "/" inside one option. Never keep the parenthetical list in the item name.
"Cabbage Salad (choice of Chicken, or Tofu)" → keep the parenthetical in the name (Python builds Choose one: Chicken, Tofu). Same for Papaya Salad (Shrimp, Beef, Tofu).
Keep translations/notes in the name: (Bún Riêu), (phở sa tế), (Mom's Recipe), (soy sauce). Those are not choice lists.
</parenthetical_choices>

<categories>
Use exact printed category names. Items belong to the last category header seen above them (including across page breaks). Never invent categories.
If a new page starts with items before any header, they belong to the previous still-open category.
ADD-ON SECTIONS ARE NOT CATEGORIES: "Extra Meat for Pho" / "Extra Protein" / "Add-Ons" / "Extras" → one checkbox group (title = printed header, printed option prices) copied onto every item of the section named in the header. Copy every printed option onto every item. A section sold on its own (TOPPING, SIDES) stays a category.
CHOICE OF MEAT banner (not itself a priced dish; Beef/Chicken/Carnitas under TACOS) → one radio copied onto EVERY separately priced dish in that category. Do not collapse the section into one item named "Taco".
A "With Cheese $N" add-on stays on the plain Taco only — not on Veggie Taco, Shrimp Taco, or Fish Taco.
"(with your choice of protein)" above V1 / G1 / R1 priced rows → keep every priced row as its own item. Category stays Vermicelli Salad / Garlic Noodle / Rice. Shared plate sentence is the description AND a Choice of Protein checkbox copied onto every item (vermicelli, lettuce, bean sprout… / steam rice, cabbage pickle…). Do not emit one item named the category. Do not put those row names in Choice of Protein.
G1 A House Garlic Noodle is its own item. Fillet (5 oz)/(10 oz) is Size on that item. "sunny side up egg as requested" is an Add-on; leave the rest of the plate sentence in the description.
Pho P1–P13 stay separate bowls. Build Your Own meats stay a checkbox. Extra Meat for Pho stays extras on every Pho item.
NOT a banner: proteins printed under ONE dish with their own prices (Super Burrito Choice of Chile Verde $14.25 / Carne Asada or Carnitas $15.95) stay on THAT dish only — not Chimichanga, combos, child's plates, or sides.
SHARED-PRICE PRODUCT LIST: "SOFT DRINK  COKE, COKE ZERO, DIET COKE, SPRITE  $3.00" → one item per product at that price, not one category-named item with a choice group.
If a description lists fillings (Chile Verde, Chile Colorado, Chicken, Shredded beef), keep the description AND extract a Choose-one radio on that item only.
Split-page layout: match name-page rows to price-table rows by position. Extract printed column prices as absolute strings.
</categories>

<dietary_tags>
V→Vegetarian, VG→Vegan, GF→Gluten-Free, GF+→Gluten-Free Option, D→Dairy, N→Nuts. Comma-separated string. "" if none.
Egg dishes are non-vegetarian unless explicitly marked otherwise. Do not infer tags from ingredients.
</dietary_tags>

<array_alignment>
option.option_id, option.name, option.price, option.unit, and options_detail MUST all have the same length.
Use "" for option_id slots and for prices with no extra charge. Never invent UUIDs.
Use exact printed text for names, descriptions, and modifier titles. No duplicate items with the same name and same price.
</array_alignment>
</rules>

Output: valid JSON array only.
"""

# ─────────────────────────────────────────────────────────────────────────────
# BOONS_EXTRACTION_PROMPT_V2 — full standalone rewrite that fixes the
# "option as a list of objects" regression seen on large menus (e.g. Kabila,
# 102/200 items had option shaped as an array-of-structs instead of the required
# object-of-parallel-arrays).
#
# ROOT CAUSE: the contract places two OPPOSITE shapes side by side —
#   option         = object of parallel arrays  {"name": [...], "price": [...]}  (uncommon)
#   options_detail = list of objects            [{"name": ..., "price": ...}]    (very common)
# Under load, Sonnet 4.6 regresses to the familiar list-of-objects form and
# reshapes `option` to match its neighbor, breaking array alignment downstream.
#
# FIX baked into this version:
#   - A dedicated <option_shape> block at the top of <rules> naming the shape,
#     showing the exact WRONG form the model tends to emit, and the CORRECT form.
#   - A one-line reminder in <array_alignment>.
# Everything else matches BOONS_EXTRACTION_PROMPT. Use get_vision_extraction_prompt().
# ─────────────────────────────────────────────────────────────────────────────

BOONS_EXTRACTION_PROMPT_V2 = """\
You are a menu data extraction system. The attached restaurant menu is provided as a PDF or image.
Extract ALL visible items from ALL pages/locations and return ONLY a valid JSON array (no markdown, no commentary).

<multi_location>
If the document contains menus from multiple locations, extract everything.
Same item + same price across locations → extract once. Same item + different price → extract both.
Do not stop at the first address or phone number.
</multi_location>

<output_format>
JSON array of category objects. Follow this example precisely:
[
  {
    "category": "APPETIZERS",
    "items": [
      {
        "name": "Spring Rolls",
        "price": 8.99,
        "description": "Crispy vegetable rolls with sweet chili sauce",
        "dietary_tags": "Vegetarian",
        "modifiers": []
      },
      {
        "name": "Chicken Wings",
        "price": 10.0,
        "description": "",
        "dietary_tags": "",
        "modifiers": [
          {
            "title": "Size",
            "item_limit": "1",
            "mandatory": "yes",
            "addonqty": "1",
            "type": "radio",
            "option": {
              "option_id": ["", ""],
              "name": ["6pc", "12pc"],
              "price": ["10", "16"],
              "unit": ["", ""]
            },
            "options_detail": [
              {"name": "6pc", "price": "10", "unit": "", "submodifier": null},
              {"name": "12pc", "price": "16", "unit": "", "submodifier": null}
            ]
          }
        ]
      },
      {
        "name": "Nachos",
        "price": 12.99,
        "description": "Tortilla chips with melted cheese",
        "dietary_tags": "Vegetarian, Gluten-Free",
        "modifiers": [
          {
            "title": "Add-ons",
            "item_limit": "0",
            "mandatory": "no",
            "addonqty": "0",
            "type": "checkbox",
            "option": {
              "option_id": ["", ""],
              "name": ["Guacamole", "Sour Cream"],
              "price": ["2.5", "1.5"],
              "unit": ["", ""]
            },
            "options_detail": [
              {"name": "Guacamole", "price": "2.5", "unit": "", "submodifier": null},
              {"name": "Sour Cream", "price": "1.5", "unit": "", "submodifier": null}
            ]
          }
        ]
      },
      {
        "name": "Paneer Tikka",
        "price": 14.99,
        "description": "Grilled cottage cheese with spices",
        "dietary_tags": "Vegetarian",
        "modifiers": [
          {
            "title": "Spice Level",
            "item_limit": "1",
            "mandatory": "yes",
            "addonqty": "1",
            "type": "radio",
            "option": {
              "option_id": ["", "", ""],
              "name": ["Mild", "Medium", "Hot"],
              "price": ["", "", ""],
              "unit": ["", "", ""]
            },
            "options_detail": [
              {
                "name": "Mild", "price": "", "unit": "",
                "submodifier": {
                  "title": "Mild", "item_limit": "1", "mandatory": "yes", "addonqty": "1", "type": "radio",
                  "option": {"option_id": ["", ""], "name": ["Low Spice", "No Chili"], "price": ["", ""], "unit": ["", ""]},
                  "options_detail": [
                    {"name": "Low Spice", "price": "", "unit": "", "submodifier": null},
                    {"name": "No Chili", "price": "", "unit": "", "submodifier": null}
                  ]
                }
              },
              {
                "name": "Medium", "price": "", "unit": "",
                "submodifier": {
                  "title": "Medium", "item_limit": "1", "mandatory": "yes", "addonqty": "1", "type": "radio",
                  "option": {"option_id": ["", ""], "name": ["Regular Spice", "Extra Masala"], "price": ["0.5", "0.75"], "unit": ["", ""]},
                  "options_detail": [
                    {"name": "Regular Spice", "price": "0.5", "unit": "", "submodifier": null},
                    {"name": "Extra Masala", "price": "0.75", "unit": "", "submodifier": null}
                  ]
                }
              },
              {
                "name": "Hot", "price": "", "unit": "",
                "submodifier": {
                  "title": "Hot", "item_limit": "1", "mandatory": "yes", "addonqty": "1", "type": "radio",
                  "option": {"option_id": ["", ""], "name": ["Spicy", "Extra Hot"], "price": ["1", "1.5"], "unit": ["", ""]},
                  "options_detail": [
                    {"name": "Spicy", "price": "1", "unit": "", "submodifier": null},
                    {"name": "Extra Hot", "price": "1.5", "unit": "", "submodifier": null}
                  ]
                }
              }
            ]
          }
        ]
      }
    ]
  }
]
</output_format>

<rules>
<option_shape>
CRITICAL — `option` is a SINGLE OBJECT of PARALLEL ARRAYS, never a list of objects.
It holds four equal-length arrays: option_id, name, price, unit. The values at index i
across all four arrays together describe one choice. Its sibling `options_detail` IS a
list of objects — do NOT copy that list shape onto `option`. This is the single most
common mistake: keep the two shapes distinct.

WRONG (a list of per-option objects — DO NOT DO THIS):
  "option": [
    {"option_id": "", "name": "Aloo", "price": "0", "unit": ""},
    {"option_id": "", "name": "Gobhi", "price": "0", "unit": ""}
  ]

CORRECT (one object, four parallel arrays):
  "option": {
    "option_id": ["", ""],
    "name": ["Aloo", "Gobhi"],
    "price": ["", ""],
    "unit": ["", ""]
  }

Every modifier carries BOTH:
  - option          → object of parallel arrays (option_id/name/price/unit), same length N
  - options_detail  → list of N objects, one per choice, same order as option.name
Also keep item_limit / mandatory / addonqty as STRINGS ("1", "yes", "0"), not numbers or booleans.
</option_shape>

<schema>
Every item MUST have all 5 fields: name (string), price (number), description (string, "" if none), dietary_tags (comma-separated string, "" if none), modifiers (array, [] if none). Never omit any field.
Do not add top-level addons or options on an item — every choice group goes in modifiers only.
</schema>

<prices>
PRICE TYPES (never mix these): item.price = NUMBER. Every modifier price
(option.price[] and options_detail[].price) = STRING. Same concept, two types — keep them distinct.
item.price is the printed dish price as a number. Use 0.0 only if truly free.
Modifier option prices are the ABSOLUTE printed price as a string ("10", "16"). Use "" if included in base or no extra charge.
You never do math. Copy printed prices verbatim; Python computes surcharges downstream.
If a mandatory size/portion group has prices and no standalone dish price, set item.price to the cheapest printed size (as a number); leave every option price as its printed absolute string.
</prices>

<modifier_triggers>
Create a modifier group when the menu shows:
- Size/portion options (Small/Medium/Large, Half/Full, 6pc/12pc)
- Protein/flavor/crust choices
- Add-on extras with price (Extra Cheese $2, Add Pav $1.5)
- Spice levels (Mild/Medium/Hot)
- Bracketed choice list: "Item(OptionA, OptionB)" → name=Item, choices become a modifier
- Inline "Add on X $N" / "Add X $N" / "extra X $N" (including after the price in parentheses) → Add-ons checkbox; trim leading Add on/Add/Extra/With from the option name
- A priced indented line under an item
- "choice of A, or B" in the name or under the dish → keep that parenthetical in the name (Python builds the required Choose one)
- A "build your own" / "choose up to N" list → checkbox of every listed choice, item_limit = N; keep the choices out of the description
- A category blurb offering an optional extra ("add an egg", "add protein") → optional Add-ons on every item in that category; strip the phrase from descriptions
- A shared "(with your choice of protein)" plate sentence over several priced rows → Choice of Protein checkbox of those ingredients on every item in the category
- "Extra: A or B $N" → two checkbox options at the same printed price
</modifier_triggers>

<modifier_types>
- radio: type="radio", mandatory="yes", item_limit="1", addonqty="1" — choose exactly one (sizes, proteins)
- checkbox: type="checkbox", mandatory="no", addonqty="0" — optional add-ons
- item_limit for checkbox = the printed MAX selectable ("up to 3" → "3"), not option count. "0" if unlimited.
</modifier_types>

<nesting>
When headings (Mild/Medium/Hot, Regular/Large) have sub-choices underneath:
- Parent option.name = headings ONLY
- Sub-choices go inside options_detail[i].submodifier (full modifier object)
- Leaf options use submodifier: null
Use flat (all submodifier: null) only when no sub-levels exist.
</nesting>

<modifier_association>
Attach a modifier only to the item directly above it until the next item name.
Copy the same group to multiple items only when:
- the menu splits "X / Y" with one shared price table below, or
- a category-level CHOICE OF MEAT banner applies to every priced dish in that category, or
- an extras section is for another dish type (Extra Meat for Pho → every Pho item).
A slash-protein list printed in one dish name (Chicken/Beef/Veggie/Shrimp (+$N)) stays on that dish only.
If association is ambiguous → modifiers: [].
Margin/sidebar/footer text that applies to all items → do not attach as a modifier to any single item.
</modifier_association>

<item_splitting>
- "A / B" with same price, no per-variant pricing → split into separate items; copy the shared modifier onto each (Dahi Bhalla/Dahi Vada)
- "A / B" with shared price table below → split items, copy the modifier to each
- "Item 6pc/12pc" with dual prices "10/16" → one item, Size radio, absolute option prices
- "Item / (5 oz) $A / (10 oz) $B" (second size may wrap) → one item, Size radio, absolute option prices
- Multi-column sizes (S/M/L or oz headers with a price per column) → one item, Size radio, every column's absolute price (keep cents)
- "A/B/C/D (+$N)" → one item, Choose-one with every slash option; only the last option gets +N, earlier options ""
- Combo "1 Bajra/1 Jowar/2 Roti (CHOOSE ONE) & 8 oz Ker Sangri Sabzi" → name="8 oz Ker Sangri Sabzi Combo"; bread is a required Choose one; do not put that printed line in the description. Same price 16 oz Papad/Gatte line stays one item with Choose Sabzi. Do not split DRY/GRAVY. Strip decorative * / ✲ and leading & from names.
- Leader dots / wrap: a $ on a continuation line belongs to THIS name, never the next dish
- Mid-line $A and right-margin $B on the same name line are BOTH this item (taco $3.95 / carne asada $4.95 → base 3.95 and a Carne Asada option; the row below keeps its own $)
- "Choice of: … $P / $Q" lines under one name are that item's protein tiers only — do not put them on the following dish
- Per-item extras stay on that item (Tostada add $2 proteins do not go on Two Flautas)
</item_splitting>

<parenthetical_choices>
"Can soda(Limca, pepsi, coke/diet)" → name="Can soda", mandatory radio title="Choose one", options split on commas. Keep "/" inside one option. Never keep the parenthetical list in the item name.
"Cabbage Salad (choice of Chicken, or Tofu)" → keep the parenthetical in the name (Python builds Choose one: Chicken, Tofu). Same for Papaya Salad (Shrimp, Beef, Tofu).
Keep translations/notes in the name: (Bún Riêu), (phở sa tế), (Mom's Recipe), (soy sauce). Those are not choice lists.
</parenthetical_choices>

<categories>
Use exact printed category names. Items belong to the last category header seen above them (including across page breaks). Never invent categories.
If a new page starts with items before any header, they belong to the previous still-open category.
An "add-on / extras" section (header names another dish type, e.g. "Extra Protein", "Add-Ons") is NOT a category — it becomes one checkbox group copied onto every item of the section named in the header. A section sold on its own (TOPPING, SIDES) stays a category.
SHARED-PRICE PRODUCT LIST: one header + a comma list of products at one price ("SOFT DRINK  COKE, DIET COKE, SPRITE  $3.00") → one item per product at that price, not one category-named item with a choice group.
If a description lists fillings, keep the description AND extract a Choose-one radio on that item only.
Split-page layout: match name-page rows to price-table rows by position. Extract printed column prices as absolute strings.
(Banner-vs-per-dish choice grouping is covered in modifier_association.)
</categories>

<dietary_tags>
V→Vegetarian, VG→Vegan, GF→Gluten-Free, GF+→Gluten-Free Option, D→Dairy, N→Nuts. Comma-separated string. "" if none.
Egg dishes are non-vegetarian unless explicitly marked otherwise. Do not infer tags from ingredients.
</dietary_tags>

<array_alignment>
option.option_id, option.name, option.price, option.unit, and options_detail MUST all have the same length.
Use "" for option_id slots and for prices with no extra charge. Never invent UUIDs.
REMINDER: `option` is ONE object of parallel arrays (option_id/name/price/unit), NOT a list of objects. `options_detail` is the list-of-objects. Keep the two shapes distinct.
Use exact printed text for names, descriptions, and modifier titles. No duplicate items with the same name and same price.
</array_alignment>
</rules>

Output: valid JSON array only.
"""

# Compact contract for text-PDF structuring. Python finishes Boons surcharge shape.
BOONS_TEXT_FAST_PROMPT = """\
Structure this restaurant menu text into JSON. Return ONLY a valid JSON array (no markdown, no commentary).

Shape per category:
{"category":"<exact>","items":[{"name":"...","price":0.0,"description":"","dietary_tags":"","modifiers":[]}]}

All item fields required. price=number. dietary_tags=string. modifiers always present ([] if none).

NESTING: When headings (Mild/Medium/Hot, Regular/Large) have children underneath, parent option.name = headings only. Sub-choices go in options_detail[i].submodifier. Flat (submodifier: null) only when no sub-levels exist.

ADD-ONS: Inline or parenthetical "Add on/Add/extra X $N" → Add-ons checkbox; strip the phrase from name/description; trim leading Add on/Add/Extra/With from the option name.

PARENTHETICAL CHOICES: "Can soda(Limca, pepsi, coke/diet, thumbsup) 2.50" → name="Can soda" + mandatory Choose one radio, options split on commas, keep "/" inside one option.

ITEM SPLITTING:
- "A / B" same price → separate items (Dahi Bhalla/Dahi Vada); copy the shared modifier onto each. Combo "…(CHOOSE ONE) & 8 oz Ker Sangri Sabzi" → name="8 oz Ker Sangri Sabzi Combo"; Choose one is the bread; description stays empty. Do not split DRY/GRAVY or 6pc/12pc. Strip leading & and decorative * / ✲ from names.
- "Angel Wings 6pc/12pc" with "10.00/16.00" → one item, Size radio, absolute option prices "10" and "16"
- "Fillet Mignon / (5 oz) $18.50 / (10 oz) $29.50" → one item, Size radio, absolute prices
- Multi-column sizes (8 oz|16 oz|32 oz or S/M/L) → one Size radio with ABSOLUTE prices on every option (keep cents). Post-processor converts to base+surcharge.
- "Cabbage Salad (choice of Chicken, or Tofu)" → keep the parenthetical in the name. Keep (Bún Riêu)/(soy sauce) in the name.
- "Build Your Own Pho" + up to 3 meats → checkbox of every printed meat, item_limit 3
- "sunny side up egg as requested" → optional Add-ons; strip from the description
- Extra Meat "A or B $N" → two checkbox options at $N
- "A/B/C/D (+$N)" → one item, Choose-one with every slash option; only the last option gets +N, earlier options ""
- Leader dots / wrap: continuation-line $ belongs to THIS name, never the next. Mid-line $A and right-margin $B are both this item. Choice-of $P/$Q under one name stay on that dish. Per-item extras stay on that item.

ADD-ON SECTIONS ARE NOT CATEGORIES: "Extra Meat for Pho" / "Extra Protein" / "Add-Ons" → checkbox group copied onto every item of the named section, every printed option included.

CATEGORY HEADER + SHARED-PRICE PRODUCTS: "SOFT DRINK  COKE, COKE ZERO, DIET COKE, SPRITE $3.00" → one item per product at that price.

CATEGORY-LEVEL CHOICE BANNER: "TACOS" then "CHOICE OF MEAT" then several priced dishes → every dish is an item and all get the same Choice of Meat radio. "(with your choice of protein)" above V1 / G1 / R1 → keep each priced row as its own item; Choice of Protein checkbox is the plate ingredients (vermicelli, lettuce, bean sprout…), copied onto every item. NOT a banner: Super Burrito "Choice of Chile Verde… $14.25 / Carne Asada or Carnitas $15.95" stays on Super Burrito only.

CATEGORY READING ORDER: last header stays open across page breaks. Items before the first header on a new page belong to the previous category. Egg dishes are non-vegetarian.

Modifier fields: title, item_limit, mandatory, addonqty, type, option{option_id,name,price,unit}, options_detail[{name,price,unit,submodifier}].
option arrays same length; option_id use "" per option; prices are strings.
Absolute option prices OK — post-processor applies base+surcharge.
Do not invent modifiers/categories. Extract ALL items. Output JSON array only.

Menu text:

"""


# ─────────────────────────────────────────────────────────────────────────────
# BOONS_TEXT_FAST_PROMPT_V2 — text-PDF (PyPDF → LLM) structuring prompt with the
# same "option shape" fix as the vision V2. This is the prompt used for text-based
# PDFs like Kabila Restaurant.pdf, which is where the 102/200 option-as-list
# regression was actually observed (Kabila is a text PDF, not vision).
#
# The original text prompt only had a terse one-liner
#   "option{option_id,name,price,unit}"
# which does NOT distinguish object-of-parallel-arrays from list-of-objects, so
# the model regressed to the list form. V2 adds an explicit OPTION SHAPE block
# (WRONG vs CORRECT) plus a reminder in the modifier-fields footer.
# Use get_boons_text_structuring_prompt().
# ─────────────────────────────────────────────────────────────────────────────

BOONS_TEXT_FAST_PROMPT_V2 = """\
Structure this restaurant menu text into JSON. Return ONLY a valid JSON array (no markdown, no commentary).

Shape per category:
{"category":"<exact>","items":[{"name":"...","price":0.0,"description":"","dietary_tags":"","modifiers":[]}]}

All item fields required. price=number. dietary_tags=string. modifiers always present ([] if none).

PRICE TYPES (never mix): item.price = NUMBER. Every modifier price (option.price[] and options_detail[].price) = STRING. You never do math — copy printed prices verbatim; Python computes surcharges.

OPTION SHAPE (CRITICAL — most common mistake):
Inside each modifier, `option` is a SINGLE OBJECT of PARALLEL ARRAYS, never a list of objects.
Its sibling `options_detail` IS a list of objects — do NOT copy that list shape onto `option`.
Also keep item_limit / mandatory / addonqty as STRINGS ("1", "yes", "0"), not numbers/booleans.

WRONG (list of per-option objects — DO NOT DO THIS):
  "option": [
    {"option_id": "", "name": "Aloo", "price": "0", "unit": ""},
    {"option_id": "", "name": "Gobhi", "price": "0", "unit": ""}
  ]

CORRECT (one object, four parallel arrays + the list-of-objects sibling):
  "option": {"option_id": ["", ""], "name": ["Aloo", "Gobhi"], "price": ["", ""], "unit": ["", ""]},
  "options_detail": [
    {"name": "Aloo", "price": "", "unit": "", "submodifier": null},
    {"name": "Gobhi", "price": "", "unit": "", "submodifier": null}
  ]

NESTING: When headings (Mild/Medium/Hot, Regular/Large) have children underneath, parent option.name = headings only. Sub-choices go in options_detail[i].submodifier. Flat (submodifier: null) only when no sub-levels exist.

ADD-ONS: Inline or parenthetical "Add on/Add/extra X $N" → Add-ons checkbox; strip the phrase from name/description; trim leading Add on/Add/Extra/With from the option name.

PARENTHETICAL CHOICES: "Can soda(Limca, pepsi, coke/diet, thumbsup) 2.50" → name="Can soda" + mandatory Choose one radio, options split on commas, keep "/" inside one option.

ITEM SPLITTING:
- "A / B" same price → separate items (Dahi Bhalla/Dahi Vada); copy the shared modifier onto each. Combo "…(CHOOSE ONE) & 8 oz Ker Sangri Sabzi" → name="8 oz Ker Sangri Sabzi Combo"; Choose one is the bread; description stays empty. Do not split DRY/GRAVY or 6pc/12pc. Strip leading & and decorative * / ✲ from names.
- "Angel Wings 6pc/12pc" with "10.00/16.00" → one item, Size radio, absolute option prices "10" and "16"
- "Fillet Mignon / (5 oz) $18.50 / (10 oz) $29.50" → one item, Size radio, absolute prices
- Multi-column sizes (8 oz|16 oz|32 oz or S/M/L) → one Size radio with ABSOLUTE prices on every option (keep cents). Post-processor converts to base+surcharge.
- "Cabbage Salad (choice of Chicken, or Tofu)" → keep the parenthetical in the name. Keep (Bún Riêu)/(soy sauce) in the name.
- "Build Your Own Pho" + up to 3 meats → checkbox of every printed meat, item_limit 3
- "sunny side up egg as requested" → optional Add-ons; strip from the description
- Extra Meat "A or B $N" → two checkbox options at $N
- "A/B/C/D (+$N)" → one item, Choose-one with every slash option; only the last option gets +N, earlier options ""
- Leader dots / wrap: continuation-line $ belongs to THIS name, never the next. Mid-line $A and right-margin $B are both this item. Choice-of $P/$Q under one name stay on that dish. Per-item extras stay on that item.

ADD-ON SECTIONS ARE NOT CATEGORIES: "Extra Meat for Pho" / "Extra Protein" / "Add-Ons" → checkbox group copied onto every item of the named section, every printed option included.

CATEGORY HEADER + SHARED-PRICE PRODUCTS: "SOFT DRINK  COKE, COKE ZERO, DIET COKE, SPRITE $3.00" → one item per product at that price.

CATEGORY-LEVEL CHOICE BANNER: "TACOS" then "CHOICE OF MEAT" then several priced dishes → every dish is an item and all get the same Choice of Meat radio. "(with your choice of protein)" above V1 / G1 / R1 → keep each priced row as its own item; Choice of Protein checkbox is the plate ingredients (vermicelli, lettuce, bean sprout…), copied onto every item. NOT a banner: Super Burrito "Choice of Chile Verde… $14.25 / Carne Asada or Carnitas $15.95" stays on Super Burrito only.

CATEGORY READING ORDER: last header stays open across page breaks. Items before the first header on a new page belong to the previous category. Egg dishes are non-vegetarian.

Modifier fields: title, item_limit, mandatory, addonqty, type, option{option_id,name,price,unit}, options_detail[{name,price,unit,submodifier}].
REMINDER: `option` is ONE object of parallel arrays, NOT a list of objects. `options_detail` is the list-of-objects. All arrays same length; option_id use "" per option.
Do not invent modifiers/categories. Extract ALL items. Output JSON array only.

Menu text:

"""


def get_vision_extraction_prompt() -> str:
    return BOONS_EXTRACTION_PROMPT_V2


def get_boons_text_structuring_prompt(raw_text: str) -> str:
    """Text-PDF prompt (V2). Compact Boons contract with explicit option-shape
    guidance; Python applies surcharge math."""
    return BOONS_TEXT_FAST_PROMPT_V2 + raw_text


# ═════════════════════════════════════════════════════════════════════════════
# V3 — MINIMAL EMISSION SHAPE (token-reduction rewrite)
#
# Motivation (measured on the 12-file sample set, 630 items / 212 modifiers):
#   - option_id: 100% empty across all 871 option slots
#   - unit:      99.7% empty
#   - options_detail: exact restatement of option.name/price + always submodifier:null
#   - description empty 39%, dietary_tags empty 48%, no modifiers 71%
# The V2 contract forces the model to emit all of that redundant, near-always-empty
# structure. V3 has the LLM emit a MINIMAL shape; expand_modifiers() in Python
# rebuilds the full Boons contract deterministically (option_id/unit/options_detail/
# addonqty). Measured output reduction: 31% (Kabila) to 50% (El Foratsero).
#
# V3 minimal item shape:
#   {"name": str, "price": number,
#    "description"?: str,        # omit when empty
#    "dietary_tags"?: str,       # omit when empty
#    "modifiers"?: [             # omit when none
#      {"title": str, "type": "radio"|"checkbox", "mandatory": "yes"|"no",
#       "item_limit": str,
#       "choices": [ {"name": str, "price"?: str, "choices"?: [...]} ]  # nested = submodifier
#      }
#    ]}
#
# Rules text is shared with V2 (single source of truth) — only the <output_format>,
# <schema>, and modifier-shape guidance change. Python re-adds everything else.
# ═════════════════════════════════════════════════════════════════════════════

# The behavioral rule sections are identical to V2; we reuse them verbatim so the
# two versions never drift. We slice them out of V2 between </output_format> and
# the closing "Output:" line, then swap in the minimal schema guidance.
_V3_RULES_START = "<rules>"
_V3_SHARED_RULES = BOONS_EXTRACTION_PROMPT_V2[
    BOONS_EXTRACTION_PROMPT_V2.index(_V3_RULES_START):
    BOONS_EXTRACTION_PROMPT_V2.index("<option_shape>")
] + BOONS_EXTRACTION_PROMPT_V2[
    BOONS_EXTRACTION_PROMPT_V2.index("<schema>"):
    BOONS_EXTRACTION_PROMPT_V2.index("</rules>") + len("</rules>")
]

BOONS_EXTRACTION_PROMPT_V3 = """\
You are a menu data extraction system. The attached restaurant menu is provided as a PDF or image.
Extract ALL visible items from ALL pages/locations and return ONLY a valid JSON array (no markdown, no commentary).

<multi_location>
If the document contains menus from multiple locations, extract everything.
Same item + same price across locations → extract once. Same item + different price → extract both.
Do not stop at the first address or phone number.
</multi_location>

<output_format>
JSON array of category objects, minimal shape. Follow this example precisely:
[
  {
    "category": "APPETIZERS",
    "items": [
      { "name": "Spring Rolls", "price": 8.99, "description": "Crispy rolls with chili sauce", "dietary_tags": "Vegetarian" },
      { "name": "Chicken Wings", "price": 10.0,
        "modifiers": [
          { "title": "Size", "type": "radio", "mandatory": "yes", "item_limit": "1",
            "choices": [ {"name": "6pc", "price": "10"}, {"name": "12pc", "price": "16"} ] }
        ] },
      { "name": "Nachos", "price": 12.99, "dietary_tags": "Vegetarian, Gluten-Free",
        "modifiers": [
          { "title": "Add-ons", "type": "checkbox", "mandatory": "no", "item_limit": "0",
            "choices": [ {"name": "Guacamole", "price": "2.5"}, {"name": "Sour Cream", "price": "1.5"} ] }
        ] },
      { "name": "Paneer Tikka", "price": 14.99, "dietary_tags": "Vegetarian",
        "modifiers": [
          { "title": "Spice Level", "type": "radio", "mandatory": "yes", "item_limit": "1",
            "choices": [
              {"name": "Mild", "choices": [ {"name": "Low Spice"}, {"name": "No Chili"} ]},
              {"name": "Medium", "choices": [ {"name": "Regular Spice", "price": "0.5"}, {"name": "Extra Masala", "price": "0.75"} ]},
              {"name": "Hot", "choices": [ {"name": "Spicy", "price": "1"}, {"name": "Extra Hot", "price": "1.5"} ]}
            ] }
        ] }
    ]
  }
]
</output_format>

<minimal_shape>
Emit ONLY these keys — nothing else. Python rebuilds the full internal contract.
Item:
  - name  : string (required)
  - price : NUMBER (required; the printed dish price; 0.0 only if truly free)
  - description  : string — OMIT the key entirely when there is no description
  - dietary_tags : comma-separated string — OMIT the key entirely when none
  - modifiers    : array — OMIT the key entirely when the item has no choices
Modifier:
  - title      : string
  - type       : "radio" (choose one) or "checkbox" (optional add-ons)
  - mandatory  : "yes" or "no" (string)
  - item_limit : string — "1" for radio; for checkbox the printed max ("up to 3" → "3"), "0" if unlimited
  - choices    : array of choice objects
Choice object:
  - name   : string (required)
  - price  : string — the ABSOLUTE printed surcharge/price — OMIT the key when the choice has no extra charge
  - choices: array — ONLY when this choice has sub-choices under it (nested group); otherwise OMIT
DO NOT emit option, option_id, options_detail, unit, addonqty, or submodifier — Python adds those.
DO NOT emit empty strings or empty arrays — omit the key instead.
PRICES: item.price = NUMBER. Every choice price = STRING. You never do math; copy printed prices verbatim.
</minimal_shape>

""" + _V3_SHARED_RULES.replace(
    # The shared <schema> block describes the full 5-field contract; V3 overrides it.
    """<schema>
Every item MUST have all 5 fields: name (string), price (number), description (string, "" if none), dietary_tags (comma-separated string, "" if none), modifiers (array, [] if none). Never omit any field.
Do not add top-level addons or options on an item — every choice group goes in modifiers only.
</schema>""",
    """<schema>
Use the minimal_shape above. Required item keys: name, price. Optional keys (description, dietary_tags, modifiers) are OMITTED when empty. Every choice group goes in modifiers only.
</schema>""",
    1,
).replace(
    # The shared <array_alignment> block is about the parallel-array option shape,
    # which V3 does not emit. Replace with a short choices reminder.
    """<array_alignment>
option.option_id, option.name, option.price, option.unit, and options_detail MUST all have the same length.
Use "" for option_id slots and for prices with no extra charge. Never invent UUIDs.
REMINDER: `option` is ONE object of parallel arrays (option_id/name/price/unit), NOT a list of objects. `options_detail` is the list-of-objects. Keep the two shapes distinct.
Use exact printed text for names, descriptions, and modifier titles. No duplicate items with the same name and same price.
</array_alignment>""",
    """<choices_reminder>
choices is a flat list of {name, price?, choices?}. Include price only when there is a surcharge. Include a nested choices array only when a choice has sub-options. Use exact printed text for names, descriptions, and titles. No duplicate items with the same name and same price.
</choices_reminder>""",
    1,
) + "\n\nOutput: valid JSON array only.\n"


BOONS_TEXT_FAST_PROMPT_V3 = """\
Structure this restaurant menu text into JSON. Return ONLY a valid JSON array (no markdown, no commentary).

MINIMAL SHAPE — emit only these keys, omit anything empty:
[{"category":"<exact>","items":[
  {"name":"...","price":0.0,
   "description":"...",           // omit key if none
   "dietary_tags":"...",         // omit key if none
   "modifiers":[                 // omit key if none
     {"title":"...","type":"radio|checkbox","mandatory":"yes|no","item_limit":"1",
      "choices":[ {"name":"...","price":"2.50"} ]   // price only if surcharge; omit otherwise
     }
   ]}
]}]

RULES:
- item.price = NUMBER (printed dish price; 0.0 only if truly free). Every choice price = STRING. You never do math — copy printed prices verbatim; Python computes surcharges.
- type: radio = choose one (item_limit "1"); checkbox = optional add-ons (item_limit = printed max "up to N" → "N", else "0").
- NESTING: a choice with sub-options carries its own nested "choices" array; leaf choices have no "choices" key.
- DO NOT emit option, option_id, options_detail, unit, addonqty, submodifier, empty strings, or empty arrays — Python adds/omits those.

ADD-ONS: Inline or parenthetical "Add on/Add/extra X $N" → Add-ons checkbox; strip the phrase from name/description; trim leading Add on/Add/Extra/With from the choice name.

PARENTHETICAL CHOICES: "Can soda(Limca, pepsi, coke/diet, thumbsup) 2.50" → name="Can soda" + mandatory Choose one radio; choices split on commas; keep "/" inside one choice.

ITEM SPLITTING:
- "A / B" same price → separate items; copy the shared modifier onto each. Do not split DRY/GRAVY or 6pc/12pc. Strip leading & and decorative * / ✲ from names.
- "Angel Wings 6pc/12pc" with "10.00/16.00" → one item, Size radio, choices 6pc "10" and 12pc "16".
- "Fillet Mignon / (5 oz) $18.50 / (10 oz) $29.50" → one item, Size radio, those absolute prices.
- Multi-column sizes (8 oz|16 oz|32 oz or S/M/L) → one Size radio, every column's absolute price (keep cents).
- "Cabbage Salad (choice of Chicken, or Tofu)" → keep the parenthetical in the name. Keep (Bún Riêu)/(soy sauce) in the name.
- "Build Your Own" + up to N → checkbox of every printed choice, item_limit "N".
- "A/B/C/D (+$N)" → one item, Choose-one; only the last choice gets price "N", earlier choices omit price.
- Leader dots / wrap: continuation-line $ belongs to THIS name. Mid-line $A and right-margin $B are both this item. Per-item extras stay on that item.

ADD-ON SECTIONS ARE NOT CATEGORIES: "Extra Meat for Pho" / "Extra Protein" / "Add-Ons" → checkbox group copied onto every item of the named section, every printed choice included.

SHARED-PRICE PRODUCT LIST: "SOFT DRINK  COKE, DIET COKE, SPRITE $3.00" → one item per product at that price.

CATEGORY-LEVEL CHOICE BANNER: "TACOS" then "CHOICE OF MEAT" then several priced dishes → every dish is an item, all get the same Choice of Meat radio. NOT a banner: proteins printed under ONE dish with their own prices stay on that dish only.

CATEGORY READING ORDER: last header stays open across page breaks. Items before the first header on a new page belong to the previous category. Egg dishes are non-vegetarian.

Do not invent modifiers/categories. Extract ALL items. Output JSON array only.

Menu text:

"""


def get_vision_extraction_prompt_v3() -> str:
    """V3 minimal-emission vision prompt. Pair with expand_modifiers()."""
    return BOONS_EXTRACTION_PROMPT_V3


def get_boons_text_structuring_prompt_v3(raw_text: str) -> str:
    """V3 minimal-emission text-PDF prompt. Pair with expand_modifiers()."""
    return BOONS_TEXT_FAST_PROMPT_V3 + raw_text
