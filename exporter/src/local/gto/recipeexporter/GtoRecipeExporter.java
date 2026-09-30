package local.gto.recipeexporter;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.gregtechceu.gtceu.api.GTCEuAPI;
import com.gregtechceu.gtceu.api.data.chemical.ChemicalHelper;
import com.gregtechceu.gtceu.api.data.chemical.material.Material;
import com.gregtechceu.gtceu.api.data.chemical.material.info.MaterialFlag;
import com.gregtechceu.gtceu.api.data.chemical.material.info.MaterialFlags;
import com.gregtechceu.gtceu.api.data.chemical.material.properties.PropertyKey;
import com.gregtechceu.gtceu.api.data.chemical.material.stack.MaterialStack;
import com.gregtechceu.gtceu.api.data.tag.TagPrefix;
import com.gregtechceu.gtceu.api.machine.MachineDefinition;
import com.gregtechceu.gtceu.api.machine.MultiblockMachineDefinition;
import com.gregtechceu.gtceu.api.machine.multiblock.PartAbility;
import com.gregtechceu.gtceu.api.pattern.BlockPattern;
import com.gregtechceu.gtceu.api.pattern.TraceabilityPredicate;
import com.gregtechceu.gtceu.api.pattern.predicates.SimplePredicate;
import com.gregtechceu.gtceu.api.recipe.GTRecipeDefinition;
import com.gregtechceu.gtceu.api.recipe.GTRecipeType;
import com.gregtechceu.gtceu.api.recipe.RecipeCondition;
import com.gregtechceu.gtceu.api.recipe.content.ChanceBoostFunction;
import com.gregtechceu.gtceu.api.recipe.content.Content;
import com.gregtechceu.gtceu.api.recipe.ingredient.FluidIngredient;
import com.gregtechceu.gtceu.api.recipe.ingredient.IntCircuitIngredient;
import com.gregtechceu.gtceu.api.recipe.ingredient.ItemIngredient;
import com.gregtechceu.gtceu.api.registry.GTRegistries;
import com.gregtechceu.gtceu.common.recipe.condition.CleanroomCondition;
import com.gtocore.api.machine.part.GTOPartAbility;
import java.io.Writer;
import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.*;
import java.util.function.Supplier;
import net.minecraft.client.Minecraft;
import net.minecraft.client.resources.language.I18n;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.material.Fluid;
import net.minecraftforge.api.distmarker.Dist;
import net.minecraftforge.event.TickEvent;
import net.minecraftforge.eventbus.api.SubscribeEvent;
import net.minecraftforge.fluids.FluidStack;
import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.fml.loading.FMLPaths;

/** Read-only full runtime dump: recipes / machines / materials / zh names -> <gamedir>/gto-dump/. */
@Mod(GtoRecipeExporter.MOD_ID)
public final class GtoRecipeExporter {
    public static final String MOD_ID = "gtorecipeexporter";
    private static int patternErrors;
    private static final Gson GSON = new GsonBuilder().disableHtmlEscaping().create();

    @Mod.EventBusSubscriber(modid = MOD_ID, value = Dist.CLIENT)
    public static final class ClientEvents {
        private static boolean done;
        private static int ticks, stable;
        private static long lastTotal = -1;

        /** Wait until the loading overlay is gone, zh_cn is live and the async recipe build has settled. */
        @SubscribeEvent
        public static void onTick(TickEvent.ClientTickEvent event) {
            if (done || event.phase != TickEvent.Phase.END || ++ticks % 40 != 0) return;
            if (Minecraft.m_91087_().m_91265_() != null) return;
            long total = 0;
            for (GTRecipeType type : GTRegistries.RECIPE_TYPES) total += type.recipes.size();
            stable = total > 0 && total == lastTotal ? stable + 1 : 0;
            lastTotal = total;
            boolean zh = "石头".equals(I18n.m_118938_("block.minecraft.stone"));
            if (stable < 5 || (!zh && ticks < 20 * 300)) return;
            done = true;
            Path out = FMLPaths.GAMEDIR.get().resolve("gto-dump");
            try {
                exportAll(out);
                log("Wrote dump to " + out + " (zh=" + zh + ")");
            } catch (Throwable t) {
                log("Export failed: " + t);
                t.printStackTrace();
            }
            if (Boolean.getBoolean("gtorecipeexporter.exit")) Runtime.getRuntime().halt(0);
        }
    }

    private static final List<String> LOG = new ArrayList<>();

    private static void log(String s) {
        LOG.add(s);
        System.out.println("[GTO Recipe Exporter] " + s);
    }

    static void exportAll(Path dir) throws Exception {
        Files.createDirectories(dir);
        JsonObject counts = new JsonObject();
        int n = 0;
        try (Writer w = Files.newBufferedWriter(dir.resolve("recipes.jsonl"), StandardCharsets.UTF_8)) {
            for (GTRecipeType type : GTRegistries.RECIPE_TYPES) {
                List<GTRecipeDefinition> list = new ArrayList<>(type.recipes.values());
                list.sort(Comparator.comparing(r -> r.id.toString()));
                for (GTRecipeDefinition r : list) {
                    try {
                        w.write(GSON.toJson(recipe(r)));
                        w.write('\n');
                        n++;
                    } catch (Throwable t) {
                        log("skip recipe " + r.id + ": " + t);
                    }
                }
            }
        }
        counts.addProperty("recipes", n);
        JsonObject machines = machines();
        counts.addProperty("machines", machines.getAsJsonArray("machines").size());
        counts.addProperty("me_parts", machines.getAsJsonArray("me_parts").size());
        write(dir.resolve("machines.json"), machines);
        JsonArray materials = materials();
        counts.addProperty("materials", materials.size());
        write(dir.resolve("materials.json"), materials);
        JsonObject lang = lang();
        counts.addProperty("lang", lang.size());
        write(dir.resolve("lang_zh.json"), lang);
        Files.writeString(dir.resolve("export.log"), String.join("\n", LOG), StandardCharsets.UTF_8);
        write(dir.resolve("meta.json"), meta(counts));
    }

    private static void write(Path p, JsonElement e) throws Exception {
        try (Writer w = Files.newBufferedWriter(p, StandardCharsets.UTF_8)) {
            GSON.toJson(e, w);
        }
    }

    // ---------------------------------------------------------------- recipes
    private static JsonObject recipe(GTRecipeDefinition r) {
        JsonObject o = new JsonObject();
        o.addProperty("id", r.id.toString());
        o.addProperty("type", r.recipeType.registryName.toString());
        o.addProperty("eut", r.eut);
        o.addProperty("tier", r.tier);
        o.addProperty("duration", r.duration);
        Integer circuit = null;
        JsonArray inItems = new JsonArray(), nc = new JsonArray();
        for (Content<ItemIngredient> c : r.itemInputs) {
            if (c.inner instanceof IntCircuitIngredient ic) {
                circuit = ic.configuration;
                continue;
            }
            JsonObject e = item(c, true);
            if (c.chance == 0) nc.add(e);
            else inItems.add(e);
        }
        JsonArray inFluids = new JsonArray();
        for (Content<FluidIngredient> c : r.fluidInputs) {
            JsonObject e = fluid(c, true);
            if (c.chance == 0) nc.add(e);
            else inFluids.add(e);
        }
        JsonArray outItems = new JsonArray(), outFluids = new JsonArray();
        r.itemOutputs.forEach(c -> outItems.add(item(c, false)));
        r.fluidOutputs.forEach(c -> outFluids.add(fluid(c, false)));
        o.addProperty("circuit", circuit);
        o.add("data", data(r));
        o.add("in_items", inItems);
        o.add("in_fluids", inFluids);
        o.add("out_items", outItems);
        o.add("out_fluids", outFluids);
        o.add("not_consumable", nc);
        ChanceBoostFunction f = r.chanceFunction;
        o.addProperty("chance_fn", f == ChanceBoostFunction.OVERCLOCK ? "OVERCLOCK"
                : f == ChanceBoostFunction.NONE ? "NONE" : f == null ? null : f.getClass().getName());
        return o;
    }

    private static JsonObject item(Content<ItemIngredient> c, boolean input) {
        JsonObject e = new JsonObject();
        LinkedHashSet<String> ids = new LinkedHashSet<>();
        for (ItemStack s : c.inner.inner.m_43908_()) ids.add(BuiltInRegistries.f_257033_.m_7981_(s.m_41720_()).toString());
        e.addProperty("key", ids.isEmpty() ? "item:?" : "item:" + ids.iterator().next());
        try {
            JsonElement j = c.inner.inner.m_43942_();
            if (j.isJsonObject() && j.getAsJsonObject().has("tag")) e.addProperty("tag", j.getAsJsonObject().get("tag").getAsString());
        } catch (Throwable ignored) {
        }
        if (ids.size() > 1) {
            JsonArray alt = new JsonArray();
            ids.stream().limit(5).forEach(alt::add);
            e.add("alternatives", alt);
        }
        amount(e, c, input);
        return e;
    }

    private static JsonObject fluid(Content<FluidIngredient> c, boolean input) {
        JsonObject e = new JsonObject();
        FluidStack[] stacks = c.inner.getStacks();
        e.addProperty("key", stacks.length == 0 ? "fluid:?" : "fluid:" + BuiltInRegistries.f_257020_.m_7981_(stacks[0].getFluid()));
        JsonElement j = c.inner.toJson();
        if (j.isJsonObject() && j.getAsJsonObject().has("tag")) e.addProperty("tag", j.getAsJsonObject().get("tag").getAsString());
        amount(e, c, input);
        return e;
    }

    private static void amount(JsonObject e, Content<?> c, boolean input) {
        e.addProperty("n", c.amount);
        if (input) {
            e.addProperty("consume", c.chance != 0);
            if (c.chance != 0 && c.chance != Content.MAX_CHANCE) e.addProperty("chance", c.chance);
        } else {
            e.addProperty("chance", c.chance);
            e.addProperty("boost", c.tierChanceBoost);
        }
    }

    private static JsonObject data(GTRecipeDefinition r) {
        JsonObject d = new JsonObject();
        if (r.data != null) r.data.fastForEach((k, v) -> {
            if (v instanceof Number num) d.addProperty(k.name, num);
            else if (v instanceof Boolean b) d.addProperty(k.name, b);
            else d.addProperty(k.name, String.valueOf(v));
        });
        JsonArray conds = new JsonArray();
        if (r.conditions != null) for (RecipeCondition c : r.conditions) {
            JsonObject co = new JsonObject();
            co.addProperty("type", c.getClass().getSimpleName());
            if (c instanceof CleanroomCondition cc && cc.cleanroom != null) d.addProperty("cleanroom", cc.cleanroom.getName());
            try {
                Component tip = c.getTooltips();
                if (tip != null) co.addProperty("text", tip.getString());
            } catch (Throwable ignored) {
            }
            conds.add(co);
        }
        if (!conds.isEmpty()) d.add("conditions", conds);
        return d;
    }

    // ---------------------------------------------------------------- machines
    private static Map<String, PartAbility> abilities() {
        Map<String, PartAbility> m = new TreeMap<>();
        for (Class<?> cls : List.of(PartAbility.class, GTOPartAbility.class)) {
            for (Field f : cls.getFields()) {
                if (!Modifier.isStatic(f.getModifiers()) || !PartAbility.class.isAssignableFrom(f.getType())) continue;
                try {
                    m.put(f.getName(), (PartAbility) f.get(null));
                } catch (Throwable ignored) {
                }
            }
        }
        return m;
    }

    private static JsonObject machines() {
        Map<String, PartAbility> abilities = abilities();
        Set<Block> partBlocks = new HashSet<>();
        abilities.values().forEach(a -> partBlocks.addAll(a.getAllBlocks()));
        JsonArray machines = new JsonArray(), meParts = new JsonArray();
        for (MachineDefinition def : GTRegistries.MACHINES.values()) {
            try {
                String id = def.getId().toString();
                String path = id.substring(id.indexOf(':') + 1);
                String zh = name(def.asStack());
                boolean multi = def instanceof MultiblockMachineDefinition;
                if (path.matches("(.*_)?me(_.*)?")) {
                    JsonObject p = new JsonObject();
                    p.addProperty("id", id);
                    p.addProperty("zh", zh);
                    p.addProperty("multiblock_part", partBlocks.contains(def.get()));
                    meParts.add(p);
                }
                GTRecipeType[] types = def.getRecipeTypes();
                if (!multi && (types == null || types.length == 0)) continue;
                JsonObject o = new JsonObject();
                o.addProperty("id", id);
                o.addProperty("zh", zh);
                o.addProperty("multiblock", multi);
                o.addProperty("tier", def.getTier());
                JsonArray rt = new JsonArray();
                if (types != null) for (GTRecipeType t : types) if (t != null) rt.add(t.registryName.toString());
                o.add("recipe_types", rt);
                if (multi) abilitiesOf((MultiblockMachineDefinition) def, abilities, o);
                JsonArray tips = new JsonArray();
                try {
                    List<Component> lines = new ArrayList<>();
                    if (def.getTooltipBuilder() != null) def.getTooltipBuilder().accept(def.asStack(), lines);
                    lines.forEach(l -> tips.add(l.getString()));
                } catch (Throwable ignored) {
                }
                o.add("tooltips_zh", tips);
                machines.add(o);
            } catch (Throwable t) {
                log("skip machine " + def + ": " + t);
            }
        }
        JsonObject root = new JsonObject();
        root.add("machines", machines);
        root.add("me_parts", meParts);
        return root;
    }

    private static void abilitiesOf(MultiblockMachineDefinition def, Map<String, PartAbility> abilities, JsonObject o) {
        Set<Block> blocks = new HashSet<>();
        try {
            Supplier<BlockPattern>[] factories = def.getPatternFactory();
            if (factories != null) for (Supplier<BlockPattern> f : factories) {
                BlockPattern p = f.get();
                if (p == null) continue;
                for (TraceabilityPredicate[][] a : p.blockMatches)
                    for (TraceabilityPredicate[] b : a)
                        for (TraceabilityPredicate tp : b) {
                            if (tp == null) continue;
                            for (List<SimplePredicate> sps : List.of(tp.common, tp.limited))
                                for (SimplePredicate sp : sps)
                                    { Block[] cs = sp.candidates == null ? null : sp.candidates.get(); if (cs != null) Collections.addAll(blocks, cs); }
                        }
            }
        } catch (Throwable t) {
            if (patternErrors++ < 5) log("pattern " + def.getId() + ": " + t);
            blocks.clear();
        }
        JsonArray arr = new JsonArray();
        abilities.forEach((name, a) -> {
            for (Block b : a.getAllBlocks()) if (blocks.contains(b)) {
                arr.add(name);
                return;
            }
        });
        o.add("part_abilities", arr);
        if (blocks.isEmpty()) o.addProperty("abilities_unknown", true);
    }

    // ---------------------------------------------------------------- materials
    private static JsonArray materials() {
        Map<String, MaterialFlag> flags = new TreeMap<>();
        for (Field f : MaterialFlags.class.getFields()) {
            try {
                if (Modifier.isStatic(f.getModifiers()) && f.get(null) instanceof MaterialFlag mf) flags.put(f.getName(), mf);
            } catch (Throwable ignored) {
            }
        }
        Collection<TagPrefix> prefixes = TagPrefix.values();
        JsonArray arr = new JsonArray();
        for (Material m : GTCEuAPI.materialManager.getRegisteredMaterials()) {
            try {
                JsonObject o = new JsonObject();
                o.addProperty("id", m.getResourceLocation().toString());
                o.addProperty("name", m.getName());
                o.addProperty("zh", m.getLocalizedName().getString());
                try {
                    o.addProperty("formula", m.getChemicalFormula().getString());
                } catch (Throwable ignored) {
                }
                JsonArray comps = new JsonArray();
                for (MaterialStack s : m.getMaterialComponents()) {
                    JsonObject c = new JsonObject();
                    c.addProperty("m", s.material().getName());
                    c.addProperty("n", s.amount());
                    comps.add(c);
                }
                o.add("components", comps);
                JsonArray fl = new JsonArray();
                flags.forEach((name, f) -> {
                    if (m.hasFlag(f)) fl.add(name);
                });
                o.add("flags", fl);
                JsonObject forms = new JsonObject();
                for (TagPrefix p : prefixes) {
                    try {
                        Item it = ChemicalHelper.getItem(p, m);
                        if (it != null && it != Items.f_41852_) forms.addProperty(p.name, BuiltInRegistries.f_257033_.m_7981_(it).toString());
                    } catch (Throwable ignored) {
                    }
                }
                o.add("forms", forms);
                if (m.hasProperty(PropertyKey.FLUID)) {
                    try {
                        Fluid fluid = m.getFluid();
                        if (fluid != null) o.addProperty("fluid", BuiltInRegistries.f_257020_.m_7981_(fluid).toString());
                    } catch (Throwable ignored) {
                    }
                }
                arr.add(o);
            } catch (Throwable t) {
                log("skip material " + m + ": " + t);
            }
        }
        return arr;
    }

    // ---------------------------------------------------------------- names / meta
    private static String name(ItemStack s) {
        try {
            return s.m_41786_().getString();
        } catch (Throwable t) {
            return null;
        }
    }

    private static JsonObject lang() {
        JsonObject o = new JsonObject();
        for (Item it : BuiltInRegistries.f_257033_) {
            String zh = name(it.m_7968_());
            if (zh != null) o.addProperty("item:" + BuiltInRegistries.f_257033_.m_7981_(it), zh);
        }
        for (Fluid f : BuiltInRegistries.f_257020_) {
            try {
                FluidStack s = new FluidStack(f, 1000);
                if (!s.isEmpty()) o.addProperty("fluid:" + BuiltInRegistries.f_257020_.m_7981_(f), s.getDisplayName().getString());
            } catch (Throwable ignored) {
            }
        }
        for (GTRecipeType t : GTRegistries.RECIPE_TYPES) {
            ResourceLocation id = t.registryName;
            String key = id.toString().replace(':', '.');
            String zh = I18n.m_118938_(key);
            if (!zh.equals(key)) o.addProperty("recipe_type:" + id, zh);
        }
        return o;
    }

    private static JsonObject meta(JsonObject counts) {
        JsonObject o = new JsonObject();
        o.addProperty("exported_at", Instant.now().toString());
        Path game = FMLPaths.GAMEDIR.get();
        // jar names carry the versions; gtceu/gtolib are jar-in-jar inside gtocore, so hash the outer jar
        try (var files = Files.list(game.resolve("mods"))) {
            for (Path p : files.filter(p -> p.getFileName().toString().startsWith("gtocore-")).toList()) {
                o.addProperty("gtocore_jar", p.getFileName().toString());
                o.addProperty("gtocore_sha256", sha256(p));
            }
        } catch (Throwable ignored) {
        }
        try {
            for (String line : Files.readAllLines(game.resolve("config/gtocore.yaml"), StandardCharsets.UTF_8))
                if (line.trim().startsWith("difficulty:")) o.addProperty("difficulty", line.split(":", 2)[1].trim());
        } catch (Throwable ignored) {
        }
        try {
            JsonObject man = com.google.gson.JsonParser.parseString(Files.readString(game.resolve("modpack.cfg"), StandardCharsets.UTF_8))
                    .getAsJsonObject().getAsJsonObject("manifest");
            o.addProperty("modpack", man.get("name").getAsString() + " " + man.get("version").getAsString());
        } catch (Throwable ignored) {
        }
        o.add("counts", counts);
        return o;
    }

    private static String sha256(Path p) throws Exception {
        byte[] h = MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(p));
        StringBuilder sb = new StringBuilder();
        for (byte b : h) sb.append(String.format("%02X", b));
        return sb.toString();
    }
}
