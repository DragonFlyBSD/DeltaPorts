diff --git content/browser/tpcd_heuristics/opener_heuristic_utils.h content/browser/tpcd_heuristics/opener_heuristic_utils.h
index f4c344420f48..791882f96861 100644
--- content/browser/tpcd_heuristics/opener_heuristic_utils.h
+++ content/browser/tpcd_heuristics/opener_heuristic_utils.h
@@ -5,6 +5,7 @@
 #ifndef CONTENT_BROWSER_TPCD_HEURISTICS_OPENER_HEURISTIC_UTILS_H_
 #define CONTENT_BROWSER_TPCD_HEURISTICS_OPENER_HEURISTIC_UTILS_H_
 
+#include <set>
 #include <map>
 
 #include "base/types/optional_ref.h"
