# Phase 2B.1 Validation Report

## Scan output checks

### entity_definitions companion_classes keys must match expected role set

OK — no issues found.

## JSONL vs DB checks

### entity_nodes missing expected columns

OK — no issues found.

### entities in JSONL but missing in entity_nodes

OK — no issues found.

### entities in entity_nodes but missing in JSONL

OK — no issues found.

### entity metadata mismatches between JSONL and entity_nodes

OK — no issues found.

## Mapping/roots checks

### entity_mappings with unknown mapping_type

OK — no issues found.

### entity_mappings missing corresponding entity_roots rows

OK — no issues found.

### entity_roots role/weight/reason mismatches

OK — no issues found.

## Structural checks

### entity_nodes missing expected columns

OK — no issues found.

### entity_nodes without ent_file

OK — no issues found.

### entity_mappings pointing to missing symbols

- `(10483, None)`
- `(10638, None)`

### entity_roots not backed by entity_mappings

OK — no issues found.

### domain entities with 0 seed roots at weight >= 0.75

- `Apxbatch`
- `Qdepositpayment`
- `GLObals`
- `Stdrepeat`
- `Schemamap`
- `Base`
- `EmployeeAging`
- `TAXSummary`

### non-domain entities with 0 seed roots at weight >= 0.75

OK — no issues found.

### unclassified entities with 0 seed roots at weight >= 0.75

OK — no issues found.

### non-domain entity counts

OK — no issues found.

### symbols acting as root for multiple entities

- `(703025, 2)`
- `(703311, 2)`
- `(706263, 2)`
- `(706405, 2)`
- `(706432, 2)`
- `(706716, 2)`
- `(706722, 2)`
- `(706803, 2)`
- `(707101, 2)`
- `(707102, 2)`
- `(707466, 2)`
- `(708188, 2)`
- `(708397, 2)`
- `(708973, 2)`
- `(709234, 2)`
- `(709928, 2)`
- `(709941, 2)`
- `(710065, 2)`
- `(710189, 2)`
- `(710444, 2)`
- `(710454, 2)`
- `(710487, 2)`
- `(711112, 2)`
- `(711167, 2)`
- `(711257, 2)`
- `(711383, 2)`
- `(711735, 2)`
- `(711746, 2)`
- `(711863, 2)`
- `(711865, 2)`
- `(711873, 2)`
- `(711894, 2)`
- `(711897, 2)`
- `(711981, 2)`
- `(712314, 2)`
- `(712398, 2)`
- `(712416, 2)`
- `(712807, 2)`
- `(712882, 2)`
- `(712962, 2)`
- `(713077, 2)`
- `(713673, 2)`
- `(713891, 2)`
- `(714036, 2)`
- `(714042, 2)`
- `(714092, 2)`
- `(714822, 2)`
- `(714824, 2)`
- `(714911, 2)`
- `(715623, 2)`
- `(715654, 2)`
- `(716201, 2)`
- `(716260, 2)`
- `(716490, 2)`
- `(716673, 2)`
- `(717434, 2)`
- `(717530, 2)`
- `(717578, 2)`
- `(717983, 2)`
- `(718013, 2)`
- `(718229, 2)`
- `(718242, 2)`
- `(718675, 2)`
- `(718885, 2)`
- `(719263, 2)`
- `(719278, 2)`
- `(719291, 2)`
- `(719465, 2)`
- `(719552, 2)`
- `(719655, 2)`
- `(719724, 2)`
- `(720237, 2)`
- `(720275, 2)`
- `(720296, 2)`
- `(720700, 2)`
- `(720954, 2)`
- `(721069, 2)`
- `(721340, 2)`
- `(721354, 2)`
- `(721547, 2)`
- `(721549, 2)`
- `(721579, 2)`
- `(721587, 2)`
- `(721832, 2)`
- `(722998, 2)`
- `(723183, 2)`
- `(723238, 2)`
- `(723334, 2)`
- `(723408, 2)`
- `(723435, 2)`
- `(723509, 2)`
- `(723543, 2)`
- `(723868, 2)`
- `(724057, 2)`
- `(724383, 2)`
- `(724460, 2)`
- `(724991, 2)`
- `(725191, 2)`
- `(725761, 2)`
- `(727144, 2)`
- `(727209, 2)`
- `(727416, 2)`
- `(727923, 2)`
- `(727948, 2)`
- `(728131, 2)`
- `(728296, 2)`
- `(728819, 2)`
- `(728829, 2)`
- `(729163, 2)`
- `(729209, 2)`
- `(729740, 2)`
- `(730322, 2)`
- `(730459, 2)`
- `(730526, 2)`
- `(730567, 2)`
- `(730685, 2)`
- `(730768, 2)`
- `(730878, 2)`
- `(730908, 2)`
- `(731064, 2)`
- `(731284, 2)`
- `(731825, 2)`
- `(732018, 2)`
- `(732076, 2)`
- `(732500, 2)`
- `(732534, 2)`
- `(733172, 2)`
- `(733184, 2)`
- `(734970, 2)`
- `(735631, 2)`
- `(736282, 2)`
- `(736365, 2)`
- `(736709, 2)`
- `(736764, 2)`
- `(736936, 2)`
- `(737005, 2)`
- `(737011, 2)`
- `(737065, 2)`
- `(737308, 2)`
- `(737564, 2)`
- `(737752, 2)`
- `(740835, 2)`
- `(740839, 2)`
- `(740876, 2)`
- `(740937, 2)`
- `(755334, 2)`
- `(755487, 2)`
- `(755659, 2)`
- `(755661, 2)`
- `(755831, 2)`
- `(755964, 2)`
- `(756089, 2)`
- `(756208, 2)`
- `(756399, 2)`
- `(756461, 2)`
- `(757291, 2)`
- `(757557, 2)`
- `(757716, 2)`
- `(758028, 2)`
- `(758293, 2)`
- `(758632, 2)`
- `(759287, 2)`
- `(759831, 2)`
- `(760777, 2)`
- `(762574, 2)`
- `(765289, 2)`
- `(776520, 2)`
- `(776873, 2)`
- `(777008, 2)`
- `(777614, 2)`
- `(777889, 2)`
- `(778362, 2)`
- `(778515, 2)`
- `(778565, 2)`
- `(778656, 2)`
- `(781184, 2)`
- `(785715, 2)`
- `(785963, 2)`
- `(785966, 2)`
- `(786679, 2)`
- `(786704, 2)`
- `(786962, 2)`
- `(787291, 2)`
- `(787811, 2)`
- `(787813, 2)`
- `(787899, 2)`
- `(788098, 2)`
- `(788126, 2)`
- `(788281, 2)`
- `(788380, 2)`
- `(788529, 2)`
- `(788641, 2)`
- `(788883, 2)`
- `(789086, 2)`
- `(789088, 2)`
- `(789444, 2)`
- `(789764, 2)`
- `(789972, 2)`
- `(789995, 2)`
- `(790374, 2)`

_(truncated — 245 total)_

## Filesystem checks

### entity_nodes missing expected columns

OK — no issues found.

### entity_nodes with .ent files missing on disk

OK — no issues found.

### companion class files referenced in entity_mappings missing on disk

OK — no issues found.

## Repo vs DB coverage

### entity_nodes missing expected columns

OK — no issues found.

### .ent files present in repo but missing in DB

OK — no issues found.

### .ent files present in DB but missing in repo

OK — no issues found.

## Role distribution

### entity_roots role distribution

- `('manager', 1796)`
- `('editor', 612)`
- `('lister', 561)`
- `('picker', 362)`
- `('allowed_operations_handler', 160)`
- `('entry_manager', 77)`
- `('form_editor', 67)`
- `('pick_manager', 53)`
- `('pick_picker', 49)`
- `('item_manager', 26)`
- `('reverse_manager', 21)`
- `('batch_manager', 8)`
- `('approval_manager', 8)`
- `('batch_picker', 6)`
- `('entity_manager', 5)`

### manager roles with unexpectedly low weight

OK — no issues found.

## Ground truth checks

### ground truth summary (derived from entity_definitions + deterministic role weights)

- `entities_with_expected_roots=1799`
- `entities_with_perfect_match=1797`
- `total_expected_symbols=3396`
- `total_actual_symbols=3394`
- `total_correct_symbols=3394`
- `precision=1.0000`
- `recall=0.9994`

### ground-truth entities missing in entity_nodes

OK — no issues found.

### entities missing expected >=0.75 roots

- `('ApprovePurchases', ['approvepurchaseseditor'])`
- `('Item', ['itemeditor'])`

### entities with unexpected >=0.75 extra roots

OK — no issues found.

