--- cc/test/layer_tree_test.cc.orig	2021-01-18 21:28:45 UTC
+++ cc/test/layer_tree_test.cc
@@ -676,7 +676,7 @@ LayerTreeTest::LayerTreeTest(viz::RendererType rendere
     init_vulkan = true;
   } else if (renderer_type_ == viz::RendererType::kSkiaDawn) {
     scoped_feature_list_.InitAndEnableFeature(features::kSkiaDawn);
-#if defined(OS_LINUX) || defined(OS_CHROMEOS)
+#if defined(OS_LINUX) || defined(OS_CHROMEOS) || defined(OS_BSD)
     init_vulkan = true;
 #elif defined(OS_WIN)
     // TODO(sgilhuly): Initialize D3D12 for Windows.
