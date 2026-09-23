// App.tsx — Agente de emails (cliente móvil, v0.1)
//
// Habla con el backend FastAPI (api.py). La URL sale de app/.env:
//   EXPO_PUBLIC_API_URL=http://192.168.1.XX:8000
// Tras cambiar el .env, reinicia `npx expo start`.

import { StatusBar } from "expo-status-bar";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  RefreshControl,
  SafeAreaView,
  StyleSheet,
  Text,
  View,
} from "react-native";

const API = (process.env.EXPO_PUBLIC_API_URL ?? "").replace(/\/$/, "");

const C = {
  papel: "#F4F5F1",
  tinta: "#1C2B45",
  tintaSuave: "#5A677C",
  linea: "#DCDFE3",
  vencida: "#C23A2B",
  pronto: "#C98200",
  luego: "#66768C",
  blanco: "#FFFFFF",
};

type Tarea = {
  ids: string[];
  accion: string | null;
  asunto: string;
  resumen: string | null;
  fecha_limite: string | null;
  repeticiones: number;
};
type Importante = {
  gmail_id: string;
  ids: string[];
  asunto: string;
  resumen: string | null;
  fecha_epoch: number;
  repeticiones: number;
};
type Descartado = { gmail_id: string; asunto: string; remitente: string; motivo: string | null };
type Pestana = "tareas" | "importantes" | "descartados";

// ── API ──────────────────────────────────────────────────────────────────
async function api<T>(ruta: string, cuerpo?: object): Promise<T> {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 10000);
  try {
    const r = await fetch(`${API}${ruta}`, {
      method: cuerpo ? "POST" : "GET",
      headers: cuerpo ? { "Content-Type": "application/json" } : undefined,
      body: cuerpo ? JSON.stringify(cuerpo) : undefined,
      signal: ctrl.signal,
    });
    if (!r.ok) throw new Error(`El servidor respondió ${r.status}`);
    return (await r.json()) as T;
  } finally {
    clearTimeout(t);
  }
}

// ── Fechas ───────────────────────────────────────────────────────────────
const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

function plazo(fecha: string | null) {
  if (!fecha) return { dia: "—", mes: "sin plazo", color: C.luego };
  const f = new Date(fecha + "T00:00:00");
  const hoy = new Date();
  hoy.setHours(0, 0, 0, 0);
  const dias = Math.round((f.getTime() - hoy.getTime()) / 86400000);
  const color = dias < 0 ? C.vencida : dias <= 7 ? C.pronto : C.luego;
  const mes = dias < 0 ? "vencida" : dias === 0 ? "hoy" : MESES[f.getMonth()];
  return { dia: String(f.getDate()), mes, color };
}

const fechaCorta = (epoch: number) => {
  const f = new Date(epoch * 1000);
  return `${f.getDate()} ${MESES[f.getMonth()]}`;
};

// ── App ──────────────────────────────────────────────────────────────────
export default function App() {
  const [pestana, setPestana] = useState<Pestana>("tareas");
  const [tareas, setTareas] = useState<Tarea[]>([]);
  const [importantes, setImportantes] = useState<Importante[]>([]);
  const [descartados, setDescartados] = useState<Descartado[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState<string | null>(null);
  const [aviso, setAviso] = useState<{ texto: string; deshacer?: () => void } | null>(null);
  const temporizador = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const cargar = useCallback(async () => {
    if (!API) {
      setError("Falta EXPO_PUBLIC_API_URL en app/.env (por ejemplo http://192.168.1.20:8000).");
      setCargando(false);
      return;
    }
    try {
      const [t, i, d] = await Promise.all([
        api<{ tareas: Tarea[] }>("/tareas"),
        api<{ importantes: Importante[] }>("/importantes?dias=7"),
        api<{ ruido: Descartado[] }>("/ruido?dias=7"),
      ]);
      setTareas(t.tareas);
      setImportantes(i.importantes);
      setDescartados(d.ruido);
      setError(null);
    } catch {
      setError(
        `No se puede conectar con ${API}. Comprueba que el servidor está arrancado ` +
          "(uvicorn api:app --host 0.0.0.0) y que el móvil está en la misma WiFi que el PC."
      );
    } finally {
      setCargando(false);
    }
  }, []);

  useEffect(() => {
    cargar();
  }, [cargar]);

  const avisar = (texto: string, deshacer?: () => void) => {
    clearTimeout(temporizador.current);
    setAviso({ texto, deshacer });
    temporizador.current = setTimeout(() => setAviso(null), 5000);
  };

  // ── Acciones ──
  const completar = async (t: Tarea) => {
    setTareas((ts) => ts.filter((x) => x !== t)); // respuesta inmediata
    try {
      await api("/tareas/completar", { ids: t.ids });
      avisar("Tarea marcada como hecha.", async () => {
        await api("/tareas/reabrir", { ids: t.ids });
        setAviso(null);
        cargar();
      });
    } catch {
      avisar("No se ha podido marcar como hecha. Inténtalo de nuevo.");
      cargar();
    }
  };

  const corregir = async (ids: string[], importante: boolean) => {
    try {
      for (const id of ids) await api(`/emails/${id}/corregir`, { importante });
      avisar(importante ? "Movido a importantes." : "Movido a descartados.", async () => {
        for (const id of ids) await api(`/emails/${id}/corregir`, { importante: !importante });
        setAviso(null);
        cargar();
      });
      cargar();
    } catch {
      avisar("No se ha podido guardar la corrección.");
    }
  };

  const enterado = async (i: Importante) => {
    setImportantes((xs) => xs.filter((x) => x !== i)); // respuesta inmediata
    try {
      await api("/emails/visto", { ids: i.ids });
      avisar("Marcado como leído.", async () => {
        await api("/emails/no-visto", { ids: i.ids });
        setAviso(null);
        cargar();
      });
    } catch {
      avisar("No se ha podido marcar como leído.");
      cargar();
    }
  };

  const revisar = async () => {
    try {
      await api("/revisar", {});
    } catch {
      avisar("Ya hay una revisión en marcha o el servidor no responde.");
      return;
    }
    setRevision("Empezando…");
    const sondeo = setInterval(async () => {
      try {
        const e = await api<{ en_curso: boolean; progreso: string; error: string | null; resultado: any }>(
          "/revisar/estado"
        );
        setRevision(e.progreso || "Revisando…");
        if (!e.en_curso) {
          clearInterval(sondeo);
          setRevision(null);
          if (e.error) avisar(`La revisión ha fallado: ${e.error}`);
          else avisar(`Bandeja al día: ${e.resultado?.nuevos ?? 0} emails nuevos.`);
          cargar();
        }
      } catch {
        clearInterval(sondeo);
        setRevision(null);
        avisar("Se ha perdido la conexión durante la revisión.");
      }
    }, 2000);
  };

  // ── Filas ──
  const filaTarea = ({ item }: { item: Tarea }) => {
    const p = plazo(item.fecha_limite);
    return (
      <View style={s.fila}>
        <View style={[s.plazo, { borderColor: p.color }]}>
          <Text style={[s.plazoDia, { color: p.color }]}>{p.dia}</Text>
          <Text style={[s.plazoMes, { color: p.color }]}>{p.mes}</Text>
        </View>
        <View style={s.cuerpo}>
          <Text style={s.titulo}>{item.accion || item.asunto}</Text>
          <Text style={s.detalle} numberOfLines={2}>
            {item.asunto}
            {item.repeticiones > 1 ? `  (${item.repeticiones} emails)` : ""}
          </Text>
        </View>
        <Pressable style={s.boton} onPress={() => completar(item)} accessibilityRole="button">
          <Text style={s.botonTexto}>Hecha</Text>
        </Pressable>
      </View>
    );
  };

  const filaImportante = ({ item }: { item: Importante }) => (
    <View style={s.fila}>
      <View style={s.cuerpo}>
        <Text style={s.fecha}>{fechaCorta(item.fecha_epoch)}</Text>
        <Text style={s.titulo}>{item.asunto}</Text>
        {item.resumen ? <Text style={s.detalle}>{item.resumen}</Text> : null}
        {item.repeticiones > 1 ? <Text style={s.detalle}>{item.repeticiones} emails sobre esto</Text> : null}
        <View style={s.acciones}>
          <Pressable style={s.botonSecundario} onPress={() => enterado(item)} accessibilityRole="button">
            <Text style={s.botonSecundarioTexto}>Me he enterado</Text>
          </Pressable>
          <Pressable onPress={() => corregir(item.ids, false)} accessibilityRole="button">
            <Text style={[s.enlace, { marginTop: 0 }]}>No me importa</Text>
          </Pressable>
        </View>
      </View>
    </View>
  );

  const filaDescartado = ({ item }: { item: Descartado }) => (
    <View style={s.fila}>
      <View style={s.cuerpo}>
        <Text style={s.tituloSuave}>{item.asunto}</Text>
        <Text style={s.detalle} numberOfLines={1}>{item.remitente}</Text>
        <Pressable onPress={() => corregir([item.gmail_id], true)} accessibilityRole="button">
          <Text style={s.enlace}>Sí me importa</Text>
        </Pressable>
      </View>
    </View>
  );

  const vacio: Record<Pestana, string> = {
    tareas: "No tienes nada pendiente. Revisa la bandeja para buscar novedades.",
    importantes: "Estás al día: no queda nada importante por leer de los últimos 7 días.",
    descartados: "Nada descartado en los últimos 7 días.",
  };

  const lista =
    pestana === "tareas" ? (
      <FlatList data={tareas} renderItem={filaTarea} keyExtractor={(t: Tarea) => t.ids.join()}
        ListEmptyComponent={<Text style={s.vacio}>{vacio.tareas}</Text>}
        refreshControl={<RefreshControl refreshing={false} onRefresh={cargar} />} />
    ) : pestana === "importantes" ? (
      <FlatList data={importantes} renderItem={filaImportante} keyExtractor={(i: Importante) => i.gmail_id}
        ListEmptyComponent={<Text style={s.vacio}>{vacio.importantes}</Text>}
        refreshControl={<RefreshControl refreshing={false} onRefresh={cargar} />} />
    ) : (
      <FlatList data={descartados} renderItem={filaDescartado} keyExtractor={(d: Descartado) => d.gmail_id}
        ListEmptyComponent={<Text style={s.vacio}>{vacio.descartados}</Text>}
        refreshControl={<RefreshControl refreshing={false} onRefresh={cargar} />} />
    );

  return (
    <SafeAreaView style={s.pantalla}>
      <StatusBar style="dark" />
      <View style={s.cabecera}>
        <Text style={s.marca}>Tu correo</Text>
        <Pressable style={[s.boton, revision ? s.botonOcupado : null]} onPress={revisar}
          disabled={!!revision} accessibilityRole="button">
          <Text style={s.botonTexto}>{revision ? "Revisando…" : "Revisar bandeja"}</Text>
        </Pressable>
      </View>
      {revision ? <Text style={s.progreso}>{revision}</Text> : null}

      <View style={s.pestanas}>
        {([
          ["tareas", `Tareas ${tareas.length}`],
          ["importantes", "Importantes"],
          ["descartados", "Descartados"],
        ] as [Pestana, string][]).map(([clave, texto]) => (
          <Pressable key={clave} onPress={() => setPestana(clave)} accessibilityRole="tab"
            accessibilityState={{ selected: pestana === clave }}
            style={[s.pestana, pestana === clave && s.pestanaActiva]}>
            <Text style={[s.pestanaTexto, pestana === clave && s.pestanaTextoActiva]}>{texto}</Text>
          </Pressable>
        ))}
      </View>

      {cargando ? <ActivityIndicator style={{ marginTop: 40 }} color={C.tinta} />
        : error ? <Text style={s.error}>{error}</Text> : lista}

      {aviso ? (
        <View style={s.aviso}>
          <Text style={s.avisoTexto}>{aviso.texto}</Text>
          {aviso.deshacer ? (
            <Pressable onPress={aviso.deshacer} accessibilityRole="button">
              <Text style={s.avisoAccion}>Deshacer</Text>
            </Pressable>
          ) : null}
        </View>
      ) : null}
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  pantalla: { flex: 1, backgroundColor: C.papel },
  cabecera: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingHorizontal: 20, paddingTop: 16, paddingBottom: 8,
  },
  marca: { fontSize: 28, fontWeight: "800", color: C.tinta, letterSpacing: -0.5 },
  progreso: { paddingHorizontal: 20, color: C.tintaSuave, fontSize: 13 },
  pestanas: { flexDirection: "row", paddingHorizontal: 12, marginTop: 8, borderBottomWidth: 1, borderColor: C.linea },
  pestana: { paddingVertical: 10, paddingHorizontal: 8, marginRight: 8, borderBottomWidth: 2, borderColor: "transparent" },
  pestanaActiva: { borderColor: C.tinta },
  pestanaTexto: { fontSize: 15, color: C.tintaSuave, fontWeight: "500" },
  pestanaTextoActiva: { color: C.tinta, fontWeight: "700" },
  fila: {
    flexDirection: "row", alignItems: "center", paddingVertical: 14, paddingHorizontal: 20,
    borderBottomWidth: 1, borderColor: C.linea,
  },
  plazo: { width: 58, alignItems: "center", paddingVertical: 4, borderLeftWidth: 4, marginRight: 14 },
  plazoDia: { fontSize: 26, fontWeight: "800", fontVariant: ["tabular-nums"] },
  plazoMes: { fontSize: 12, fontWeight: "600" },
  cuerpo: { flex: 1 },
  fecha: { fontSize: 12, color: C.tintaSuave, marginBottom: 2, fontVariant: ["tabular-nums"] },
  titulo: { fontSize: 16, fontWeight: "700", color: C.tinta, lineHeight: 21 },
  tituloSuave: { fontSize: 15, fontWeight: "500", color: C.tinta, lineHeight: 20 },
  detalle: { fontSize: 13, color: C.tintaSuave, marginTop: 3, lineHeight: 18 },
  enlace: { fontSize: 13, color: C.tinta, fontWeight: "700", marginTop: 8, textDecorationLine: "underline" },
  acciones: { flexDirection: "row", alignItems: "center", gap: 18, marginTop: 10 },
  botonSecundario: { borderWidth: 1.5, borderColor: C.tinta, borderRadius: 6, paddingVertical: 6, paddingHorizontal: 12 },
  botonSecundarioTexto: { color: C.tinta, fontWeight: "700", fontSize: 13 },
  boton: { backgroundColor: C.tinta, paddingVertical: 9, paddingHorizontal: 14, borderRadius: 6, marginLeft: 10 },
  botonOcupado: { opacity: 0.6 },
  botonTexto: { color: C.blanco, fontWeight: "700", fontSize: 14 },
  vacio: { padding: 24, color: C.tintaSuave, fontSize: 15, lineHeight: 21 },
  error: { margin: 20, padding: 14, color: C.vencida, fontSize: 14, lineHeight: 20, borderLeftWidth: 4, borderColor: C.vencida },
  aviso: {
    position: "absolute", left: 16, right: 16, bottom: 28, backgroundColor: C.tinta, borderRadius: 8,
    paddingVertical: 12, paddingHorizontal: 16, flexDirection: "row", justifyContent: "space-between", alignItems: "center",
  },
  avisoTexto: { color: C.blanco, fontSize: 14, flex: 1 },
  avisoAccion: { color: C.blanco, fontWeight: "800", fontSize: 14, marginLeft: 16, textDecorationLine: "underline" },
});
