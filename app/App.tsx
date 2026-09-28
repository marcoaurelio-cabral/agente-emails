// App.tsx — Agente de emails (cliente móvil, v0.2)
//
// La URL del backend sale de app/.env:
//   EXPO_PUBLIC_API_URL=http://localhost:8000      (web en el PC)
//   EXPO_PUBLIC_API_URL=http://192.168.1.XX:8000   (móvil en la misma WiFi)
// Tras cambiar el .env:  npx expo start --web -c

import { Ionicons } from "@expo/vector-icons";
import { StatusBar } from "expo-status-bar";
import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Animated,
  Easing,
  FlatList,
  Platform,
  Pressable,
  RefreshControl,
  SafeAreaView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

const API_ENV = (process.env.EXPO_PUBLIC_API_URL ?? "").replace(/\/$/, "");
// Compilada y servida por el propio backend (el acceso directo), la API está en
// la misma dirección que la app: rutas relativas. En desarrollo (Expo, puerto
// 8081) se usa la URL del .env.
const MISMO_ORIGEN =
  Platform.OS === "web" && (globalThis as any).location?.port !== "8081";
const API = MISMO_ORIGEN ? "" : API_ENV;
const NATIVO = Platform.OS !== "web"; // el driver nativo de Animated no existe en web

// ── Tokens de diseño ──────────────────────────────────────────────────────
const C = {
  fondo: "#EEF1F8",
  tarjeta: "#FFFFFF",
  tinta: "#131F3A",
  tintaSuave: "#56627A",
  linea: "#DDE2EE",
  blanco: "#FFFFFF",
  vencida: "#E5484D",
  pronto: "#F59E0B",
  luego: "#3E63DD",
  hecha: "#12A150",
  primario: "#2F5BEA",
};
const CATEGORIA: Record<string, { color: string; nombre: string }> = {
  beca: { color: "#7C3AED", nombre: "Beca" },
  evento: { color: "#0891B2", nombre: "Evento" },
  viaje: { color: "#059669", nombre: "Viaje" },
  personal: { color: "#EA580C", nombre: "Personal" },
  dinero: { color: "#CA8A04", nombre: "Dinero" },
  empleo_afin: { color: "#DB2777", nombre: "Empleo" },
};
const categoria = (c?: string | null) => CATEGORIA[c ?? ""] ?? { color: C.luego, nombre: "Aviso" };

// ── Tipos ─────────────────────────────────────────────────────────────────
type Tarea = {
  ids: string[];
  accion: string | null;
  asunto: string;
  categoria: string | null;
  fecha_limite: string | null;
  repeticiones: number;
};
type Importante = {
  gmail_id: string;
  ids: string[];
  asunto: string;
  resumen: string | null;
  categoria: string | null;
  fecha_epoch: number;
  repeticiones: number;
};
type Descartado = { gmail_id: string; asunto: string; remitente: string };
type Pestana = "tareas" | "importantes" | "descartados";
type Salida = (direccion: 1 | -1, despues: () => void) => void;

// ── API ───────────────────────────────────────────────────────────────────
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

// ── Fechas ────────────────────────────────────────────────────────────────
const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

function plazo(fecha: string | null) {
  if (!fecha) return { dia: "·", mes: "sin plazo", color: C.luego, dias: Infinity };
  const f = new Date(fecha + "T00:00:00");
  const hoy = new Date();
  hoy.setHours(0, 0, 0, 0);
  const dias = Math.round((f.getTime() - hoy.getTime()) / 86400000);
  const color = dias < 0 ? C.vencida : dias <= 7 ? C.pronto : C.luego;
  const mes = dias < 0 ? "vencida" : dias === 0 ? "hoy" : dias === 1 ? "mañana" : MESES[f.getMonth()];
  return { dia: String(f.getDate()), mes, color, dias };
}

// Fechas rápidas para convertir un importante en tarea.
const OPCIONES_FECHA: [string, number | null][] = [
  ["Sin fecha", null], ["Hoy", 0], ["Mañana", 1], ["En 3 días", 3], ["En una semana", 7],
];
const dentroDe = (dias: number) => {
  const f = new Date();
  f.setDate(f.getDate() + dias);
  return `${f.getFullYear()}-${String(f.getMonth() + 1).padStart(2, "0")}-${String(f.getDate()).padStart(2, "0")}`;
};

const fechaCorta = (epoch: number) => {
  const f = new Date(epoch * 1000);
  return `${f.getDate()} ${MESES[f.getMonth()]}`;
};

// ── Tarjeta animada ───────────────────────────────────────────────────────
// Entra escalonada al aparecer y sale deslizándose cuando actúas sobre ella.
function Tarjeta({ indice, children }: { indice: number; children: (salir: Salida) => ReactNode }) {
  const entrada = useRef(new Animated.Value(0)).current;
  const x = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    Animated.timing(entrada, {
      toValue: 1,
      duration: 340,
      delay: Math.min(indice, 8) * 55,
      easing: Easing.out(Easing.cubic),
      useNativeDriver: NATIVO,
    }).start();
  }, [entrada, indice]);

  const salir: Salida = (direccion, despues) =>
    Animated.timing(x, {
      toValue: direccion * 480,
      duration: 260,
      easing: Easing.in(Easing.cubic),
      useNativeDriver: NATIVO,
    }).start(() => despues());

  const estilo = {
    opacity: Animated.multiply(entrada, x.interpolate({ inputRange: [-480, 0, 480], outputRange: [0, 1, 0] })),
    transform: [
      { translateY: entrada.interpolate({ inputRange: [0, 1], outputRange: [20, 0] }) },
      { translateX: x },
    ],
  };
  return <Animated.View style={[s.tarjeta, estilo]}>{children(salir)}</Animated.View>;
}

// ── Botón de revisar ──────────────────────────────────────────────────────
// Pastilla flotante. Al revisar, un anillo discontinuo gira alrededor del
// sobre y el texto muestra el progreso real del servidor.
function BotonRevisar({ progreso, onPress }: { progreso: string | null; onPress: () => void }) {
  const giro = useRef(new Animated.Value(0)).current;
  const pulsado = useRef(new Animated.Value(1)).current;

  useEffect(() => {
    if (!progreso) {
      giro.stopAnimation();
      giro.setValue(0);
      return;
    }
    const bucle = Animated.loop(
      Animated.timing(giro, { toValue: 1, duration: 1400, easing: Easing.linear, useNativeDriver: NATIVO })
    );
    bucle.start();
    return () => bucle.stop();
  }, [progreso, giro]);

  const presionar = (a: number) =>
    Animated.spring(pulsado, { toValue: a, useNativeDriver: NATIVO, speed: 40, bounciness: 8 }).start();
  const rotacion = giro.interpolate({ inputRange: [0, 1], outputRange: ["0deg", "360deg"] });

  return (
    <Animated.View style={[s.fabZona, { transform: [{ scale: pulsado }] }]}>
      <Pressable
        onPress={onPress}
        onPressIn={() => presionar(0.95)}
        onPressOut={() => presionar(1)}
        disabled={!!progreso}
        accessibilityRole="button"
        accessibilityLabel={progreso ? `Revisando: ${progreso}` : "Revisar bandeja"}
        style={[s.fab, progreso ? s.fabOcupado : null]}
      >
        <View style={s.fabIcono}>
          {progreso ? <Animated.View style={[s.fabAnillo, { transform: [{ rotate: rotacion }] }]} /> : null}
          <Ionicons name={progreso ? "mail-open-outline" : "mail-unread-outline"} size={20} color={C.blanco} />
        </View>
        <Text style={s.fabTexto} numberOfLines={1}>{progreso ?? "Revisar bandeja"}</Text>
      </Pressable>
    </Animated.View>
  );
}

// ── App ───────────────────────────────────────────────────────────────────
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
  const [convirtiendo, setConvirtiendo] = useState<string | null>(null);
  const [accionNueva, setAccionNueva] = useState("");
  const [fechaNueva, setFechaNueva] = useState<string | null>(null);

  const cargar = useCallback(async () => {
    if (!API && !MISMO_ORIGEN) {
      setError("Falta EXPO_PUBLIC_API_URL en app/.env (por ejemplo http://localhost:8000).");
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
      setError(`No se puede conectar con ${API || "el servidor"}. Si usas el acceso directo, ciérralo y vuelve a abrirlo; si estás desarrollando, arranca uvicorn.`);
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
  const completar = (t: Tarea, salir: Salida) =>
    salir(1, async () => {
      setTareas((ts) => ts.filter((x) => x !== t));
      try {
        await api("/tareas/completar", { ids: t.ids });
        avisar("Tarea marcada como hecha.", async () => {
          await api("/tareas/reabrir", { ids: t.ids });
          setAviso(null);
          cargar();
        });
      } catch {
        avisar("No se ha podido marcar como hecha.");
        cargar();
      }
    });

  const enterado = (i: Importante, salir: Salida) =>
    salir(1, async () => {
      setImportantes((xs) => xs.filter((x) => x !== i));
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
    });

  const descartarTarea = (t: Tarea, salir: Salida) =>
    salir(-1, async () => {
      setTareas((ts) => ts.filter((x) => x !== t));
      try {
        await api("/tareas/descartar", { ids: t.ids });
        avisar("Tarea descartada.", async () => {
          await api("/tareas/reabrir", { ids: t.ids });
          setAviso(null);
          cargar();
        });
      } catch {
        avisar("No se ha podido descartar.");
        cargar();
      }
    });

  const abrirConversion = (i: Importante) => {
    setConvirtiendo(i.gmail_id);
    setAccionNueva("");
    setFechaNueva(null);
  };

  const guardarTarea = (i: Importante, salir: Salida) => {
    const accion = accionNueva.trim();
    if (!accion) {
      avisar("Escribe qué tienes que hacer.");
      return;
    }
    salir(1, async () => {
      setConvirtiendo(null);
      setImportantes((xs) => xs.filter((x) => x !== i));
      try {
        await api("/emails/tarea", { ids: i.ids, accion, fecha_limite: fechaNueva });
        avisar("Añadida a tus tareas.", async () => {
          await api("/emails/quitar-tarea", { ids: i.ids });
          setAviso(null);
          cargar();
        });
        cargar();
      } catch {
        avisar("No se ha podido crear la tarea.");
        cargar();
      }
    });
  };

  const corregir = async (ids: string[], importante: boolean, salir?: Salida) => {
    const hacer = async () => {
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
    salir ? salir(-1, hacer) : hacer();
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
        setRevision(e.progreso ? e.progreso.charAt(0).toUpperCase() + e.progreso.slice(1) : "Revisando…");
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

  // ── Resumen de cabecera ──
  const vencidas = tareas.filter((t) => plazo(t.fecha_limite).dias < 0).length;
  const resumen =
    tareas.length === 0 && importantes.length === 0
      ? "Estás al día."
      : `${tareas.length} ${tareas.length === 1 ? "tarea pendiente" : "tareas pendientes"}` +
        (vencidas ? `, ${vencidas} ${vencidas === 1 ? "vencida" : "vencidas"}` : "") +
        ` y ${importantes.length} ${importantes.length === 1 ? "aviso" : "avisos"} por leer.`;

  // ── Filas ──
  const filaTarea = ({ item, index }: { item: Tarea; index: number }) => {
    const p = plazo(item.fecha_limite);
    return (
      <Tarjeta indice={index}>
        {(salir) => (
          <View style={s.filaTarea}>
            <View style={[s.pestanaFecha, { backgroundColor: p.color }]}>
              <Text style={s.fechaDia}>{p.dia}</Text>
              <Text style={s.fechaMes}>{p.mes}</Text>
            </View>
            <View style={s.cuerpo}>
              <Text style={s.titulo}>{item.accion || item.asunto}</Text>
              <Text style={s.detalle} numberOfLines={2}>{item.asunto}</Text>
              {item.repeticiones > 1 ? <Text style={s.meta}>{item.repeticiones} emails sobre esto</Text> : null}
            </View>
            <Pressable style={s.botonIcono} onPress={() => descartarTarea(item, salir)} accessibilityRole="button"
              accessibilityLabel={`Descartar: ${item.accion || item.asunto}`}>
              <Ionicons name="close" size={18} color={C.tintaSuave} />
            </Pressable>
            <Pressable style={s.botonHecha} onPress={() => completar(item, salir)} accessibilityRole="button"
              accessibilityLabel={`Marcar como hecha: ${item.accion || item.asunto}`}>
              <Ionicons name="checkmark" size={18} color={C.blanco} />
              <Text style={s.botonHechaTexto}>Hecha</Text>
            </Pressable>
          </View>
        )}
      </Tarjeta>
    );
  };

  const filaImportante = ({ item, index }: { item: Importante; index: number }) => {
    const cat = categoria(item.categoria);
    return (
      <Tarjeta indice={index}>
        {(salir) => (
          <View style={[s.filaImportante, { borderTopColor: cat.color }]}>
            <View style={s.cabeceraTarjeta}>
              <View style={[s.etiqueta, { backgroundColor: cat.color + "1A" }]}>
                <View style={[s.punto, { backgroundColor: cat.color }]} />
                <Text style={[s.etiquetaTexto, { color: cat.color }]}>{cat.nombre}</Text>
              </View>
              <Text style={s.fechaCorta}>{fechaCorta(item.fecha_epoch)}</Text>
            </View>
            <Text style={s.titulo}>{item.asunto}</Text>
            {item.resumen ? <Text style={s.resumen}>{item.resumen}</Text> : null}
            {item.repeticiones > 1 ? <Text style={s.meta}>{item.repeticiones} emails sobre esto</Text> : null}
            {convirtiendo === item.gmail_id ? (
              <View style={s.editor}>
                <Text style={s.editorEtiqueta}>¿Qué tienes que hacer?</Text>
                <TextInput style={s.entrada} value={accionNueva} onChangeText={setAccionNueva} autoFocus
                  placeholder="Por ejemplo: rellenar el formulario de la beca" placeholderTextColor="#98A2B3"
                  maxLength={200} onSubmitEditing={() => guardarTarea(item, salir)} />
                <Text style={s.editorEtiqueta}>¿Para cuándo?</Text>
                <View style={s.chips}>
                  {OPCIONES_FECHA.map(([texto, dias]) => {
                    const valor = dias === null ? null : dentroDe(dias);
                    const activo = fechaNueva === valor;
                    return (
                      <Pressable key={texto} onPress={() => setFechaNueva(valor)} accessibilityRole="radio"
                        accessibilityState={{ checked: activo }} style={[s.chip, activo && s.chipActivo]}>
                        <Text style={[s.chipTexto, activo && s.chipTextoActivo]}>{texto}</Text>
                      </Pressable>
                    );
                  })}
                </View>
                <View style={s.acciones}>
                  <Pressable style={s.botonEnterado} onPress={() => guardarTarea(item, salir)} accessibilityRole="button">
                    <Ionicons name="checkmark" size={16} color={C.blanco} />
                    <Text style={s.botonEnteradoTexto}>Guardar tarea</Text>
                  </Pressable>
                  <Pressable onPress={() => setConvirtiendo(null)} accessibilityRole="button">
                    <Text style={s.enlace}>Cancelar</Text>
                  </Pressable>
                </View>
              </View>
            ) : (
              <View style={s.acciones}>
                <Pressable style={s.botonEnterado} onPress={() => enterado(item, salir)} accessibilityRole="button">
                  <Ionicons name="eye-outline" size={16} color={C.blanco} />
                  <Text style={s.botonEnteradoTexto}>Me he enterado</Text>
                </Pressable>
                <Pressable style={s.botonContorno} onPress={() => abrirConversion(item)} accessibilityRole="button">
                  <Ionicons name="add-circle-outline" size={16} color={C.tinta} />
                  <Text style={s.botonContornoTexto}>Hacer tarea</Text>
                </Pressable>
                <Pressable onPress={() => corregir(item.ids, false, salir)} accessibilityRole="button">
                  <Text style={s.enlace}>No me importa</Text>
                </Pressable>
              </View>
            )}
          </View>
        )}
      </Tarjeta>
    );
  };

  const filaDescartado = ({ item }: { item: Descartado }) => (
    <View style={s.filaDescartado}>
      <View style={s.cuerpo}>
        <Text style={s.tituloDescartado} numberOfLines={2}>{item.asunto}</Text>
        <Text style={s.detalle} numberOfLines={1}>{item.remitente}</Text>
      </View>
      <Pressable onPress={() => corregir([item.gmail_id], true)} accessibilityRole="button">
        <Text style={s.enlace}>Sí me importa</Text>
      </Pressable>
    </View>
  );

  const vacio = (icono: keyof typeof Ionicons.glyphMap, texto: string) => (
    <View style={s.vacio}>
      <Ionicons name={icono} size={40} color={C.luego} />
      <Text style={s.vacioTexto}>{texto}</Text>
    </View>
  );

  const refresco = <RefreshControl refreshing={false} onRefresh={cargar} />;
  const lista =
    pestana === "tareas" ? (
      <FlatList data={tareas} renderItem={filaTarea} keyExtractor={(t: Tarea) => t.ids.join()}
        contentContainerStyle={s.contenido} refreshControl={refresco}
        ListEmptyComponent={vacio("checkmark-done-circle-outline", "No tienes nada pendiente. Revisa la bandeja para buscar novedades.")} />
    ) : pestana === "importantes" ? (
      <FlatList data={importantes} renderItem={filaImportante} keyExtractor={(i: Importante) => i.gmail_id}
        extraData={[convirtiendo, accionNueva, fechaNueva]}
        contentContainerStyle={s.contenido} refreshControl={refresco}
        ListEmptyComponent={vacio("sparkles-outline", "Estás al día: no queda nada importante por leer de los últimos 7 días.")} />
    ) : (
      <FlatList data={descartados} renderItem={filaDescartado} keyExtractor={(d: Descartado) => d.gmail_id}
        contentContainerStyle={s.contenido} refreshControl={refresco}
        ListEmptyComponent={vacio("file-tray-outline", "Nada descartado en los últimos 7 días.")} />
    );

  const pestanas: [Pestana, string, number | null][] = [
    ["tareas", "Tareas", tareas.length],
    ["importantes", "Importantes", importantes.length],
    ["descartados", "Descartados", null],
  ];

  return (
    <SafeAreaView style={s.pantalla}>
      <StatusBar style="dark" />
      <View style={s.cabecera}>
        <Text style={s.marca}>Tu correo</Text>
        {!cargando && !error ? <Text style={s.resumenCabecera}>{resumen}</Text> : null}
      </View>

      <View style={s.segmentos}>
        {pestanas.map(([clave, texto, n]) => {
          const activa = pestana === clave;
          return (
            <Pressable key={clave} onPress={() => setPestana(clave)} accessibilityRole="tab"
              accessibilityState={{ selected: activa }} style={[s.segmento, activa && s.segmentoActivo]}>
              <Text style={[s.segmentoTexto, activa && s.segmentoTextoActivo]}>{texto}</Text>
              {n ? (
                <View style={[s.contador, activa && s.contadorActivo]}>
                  <Text style={[s.contadorTexto, activa && s.contadorTextoActivo]}>{n}</Text>
                </View>
              ) : null}
            </Pressable>
          );
        })}
      </View>

      {cargando ? <ActivityIndicator style={{ marginTop: 48 }} color={C.primario} size="large" />
        : error ? (
          <View style={s.error}>
            <Ionicons name="cloud-offline-outline" size={22} color={C.vencida} />
            <Text style={s.errorTexto}>{error}</Text>
            <Pressable onPress={() => { setCargando(true); cargar(); }} accessibilityRole="button">
              <Text style={s.enlace}>Reintentar</Text>
            </Pressable>
          </View>
        ) : lista}

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

      {!error ? <BotonRevisar progreso={revision} onPress={revisar} /> : null}
    </SafeAreaView>
  );
}

// ── Estilos ───────────────────────────────────────────────────────────────
const sombra = {
  shadowColor: "#1B2B5E",
  shadowOpacity: 0.08,
  shadowRadius: 14,
  shadowOffset: { width: 0, height: 6 },
  elevation: 3,
};

const s = StyleSheet.create({
  pantalla: { flex: 1, backgroundColor: C.fondo },
  cabecera: { paddingHorizontal: 20, paddingTop: 20, paddingBottom: 6 },
  marca: { fontSize: 32, fontWeight: "800", color: C.tinta, letterSpacing: -0.8 },
  resumenCabecera: { fontSize: 15, color: C.tintaSuave, marginTop: 4, lineHeight: 21 },

  segmentos: {
    flexDirection: "row", marginHorizontal: 16, marginTop: 14, marginBottom: 4, padding: 4,
    backgroundColor: "#E2E7F2", borderRadius: 12,
  },
  segmento: {
    flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center",
    paddingVertical: 9, borderRadius: 9, gap: 6,
  },
  segmentoActivo: { backgroundColor: C.tarjeta, ...sombra, shadowOpacity: 0.06 },
  segmentoTexto: { fontSize: 14, fontWeight: "600", color: C.tintaSuave },
  segmentoTextoActivo: { color: C.tinta, fontWeight: "800" },
  contador: { minWidth: 20, paddingHorizontal: 6, height: 20, borderRadius: 10, backgroundColor: "#CBD3E6", alignItems: "center", justifyContent: "center" },
  contadorActivo: { backgroundColor: C.primario },
  contadorTexto: { fontSize: 12, fontWeight: "800", color: C.tinta, fontVariant: ["tabular-nums"] },
  contadorTextoActivo: { color: C.blanco },

  contenido: { padding: 16, paddingBottom: 120, gap: 12 },
  tarjeta: { backgroundColor: C.tarjeta, borderRadius: 16, borderWidth: 1, borderColor: C.linea, overflow: "hidden", ...sombra },

  filaTarea: { flexDirection: "row", alignItems: "center", padding: 12, gap: 14 },
  pestanaFecha: { width: 62, paddingVertical: 10, borderRadius: 12, alignItems: "center" },
  fechaDia: { fontSize: 26, fontWeight: "900", color: C.blanco, fontVariant: ["tabular-nums"], lineHeight: 30 },
  fechaMes: { fontSize: 12, fontWeight: "700", color: C.blanco },
  cuerpo: { flex: 1 },
  titulo: { fontSize: 16, fontWeight: "800", color: C.tinta, lineHeight: 21 },
  detalle: { fontSize: 13, color: C.tintaSuave, marginTop: 3, lineHeight: 18 },
  meta: { fontSize: 12, color: C.luego, fontWeight: "700", marginTop: 6 },
  botonHecha: {
    flexDirection: "row", alignItems: "center", gap: 4, backgroundColor: C.hecha,
    paddingVertical: 9, paddingHorizontal: 12, borderRadius: 999,
  },
  botonHechaTexto: { color: C.blanco, fontWeight: "800", fontSize: 14 },

  filaImportante: { padding: 16, borderTopWidth: 4 },
  cabeceraTarjeta: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 8 },
  etiqueta: { flexDirection: "row", alignItems: "center", gap: 6, paddingVertical: 4, paddingHorizontal: 9, borderRadius: 6 },
  punto: { width: 7, height: 7, borderRadius: 4 },
  etiquetaTexto: { fontSize: 12, fontWeight: "800" },
  fechaCorta: { fontSize: 12, color: C.tintaSuave, fontWeight: "600", fontVariant: ["tabular-nums"] },
  resumen: { fontSize: 14, color: C.tintaSuave, marginTop: 6, lineHeight: 20 },
  acciones: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 14, rowGap: 10, marginTop: 14 },
  botonIcono: {
    width: 38, height: 38, borderRadius: 19, borderWidth: 1, borderColor: C.linea,
    alignItems: "center", justifyContent: "center",
  },
  botonContorno: {
    flexDirection: "row", alignItems: "center", gap: 6, borderWidth: 1.5, borderColor: C.tinta,
    paddingVertical: 8, paddingHorizontal: 12, borderRadius: 999,
  },
  botonContornoTexto: { color: C.tinta, fontWeight: "800", fontSize: 13 },
  editor: { marginTop: 14, padding: 14, backgroundColor: "#F3F6FD", borderRadius: 12, gap: 8 },
  editorEtiqueta: { fontSize: 13, fontWeight: "800", color: C.tinta },
  entrada: {
    backgroundColor: C.blanco, borderWidth: 1, borderColor: C.linea, borderRadius: 10,
    paddingHorizontal: 12, paddingVertical: 10, fontSize: 15, color: C.tinta,
  },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: { paddingVertical: 7, paddingHorizontal: 12, borderRadius: 999, borderWidth: 1, borderColor: C.linea, backgroundColor: C.blanco },
  chipActivo: { backgroundColor: C.primario, borderColor: C.primario },
  chipTexto: { fontSize: 13, fontWeight: "700", color: C.tinta },
  chipTextoActivo: { color: C.blanco },
  botonEnterado: {
    flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: C.tinta,
    paddingVertical: 9, paddingHorizontal: 14, borderRadius: 999,
  },
  botonEnteradoTexto: { color: C.blanco, fontWeight: "800", fontSize: 13 },

  filaDescartado: {
    flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 12, paddingHorizontal: 4,
    borderBottomWidth: 1, borderColor: C.linea,
  },
  tituloDescartado: { fontSize: 14, fontWeight: "500", color: C.tinta, lineHeight: 19 },
  enlace: { fontSize: 13, color: C.primario, fontWeight: "800", textDecorationLine: "underline" },

  vacio: { alignItems: "center", paddingTop: 56, paddingHorizontal: 32, gap: 12 },
  vacioTexto: { fontSize: 15, color: C.tintaSuave, textAlign: "center", lineHeight: 21 },
  error: { margin: 16, padding: 16, borderRadius: 14, backgroundColor: "#FDECEC", gap: 10, alignItems: "flex-start" },
  errorTexto: { color: "#9B1C1C", fontSize: 14, lineHeight: 20 },

  aviso: {
    position: "absolute", left: 16, right: 16, bottom: 96, backgroundColor: C.tinta, borderRadius: 12,
    paddingVertical: 12, paddingHorizontal: 16, flexDirection: "row", justifyContent: "space-between", alignItems: "center",
    ...sombra,
  },
  avisoTexto: { color: C.blanco, fontSize: 14, flex: 1 },
  avisoAccion: { color: "#9DB4FF", fontWeight: "800", fontSize: 14, marginLeft: 16 },

  fabZona: { position: "absolute", bottom: 28, alignSelf: "center" },
  fab: {
    flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: C.primario,
    paddingVertical: 12, paddingLeft: 12, paddingRight: 20, borderRadius: 999, maxWidth: 320,
    shadowColor: C.primario, shadowOpacity: 0.35, shadowRadius: 18, shadowOffset: { width: 0, height: 8 }, elevation: 6,
  },
  fabOcupado: { backgroundColor: C.tinta, shadowColor: C.tinta },
  fabIcono: { width: 34, height: 34, borderRadius: 17, backgroundColor: "rgba(255,255,255,0.18)", alignItems: "center", justifyContent: "center" },
  fabAnillo: {
    position: "absolute", width: 34, height: 34, borderRadius: 17,
    borderWidth: 2, borderColor: C.blanco, borderStyle: "dashed",
  },
  fabTexto: { color: C.blanco, fontWeight: "800", fontSize: 15, flexShrink: 1 },
});
