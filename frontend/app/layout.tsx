import type { Metadata } from "next";
import "maplibre-gl/dist/maplibre-gl.css";
import "./globals.css";
import "./themes.css";
import { ThemeProvider } from "./components/theme-switcher";
import { themeBootstrap } from "../lib/themes";

export const metadata: Metadata = { title: "Попутно — план путешествия", description: "Маршрут с учётом интересов всей группы" };
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="ru" data-theme="warm" suppressHydrationWarning><head><script dangerouslySetInnerHTML={{__html:themeBootstrap}}/></head><body><ThemeProvider>{children}</ThemeProvider></body></html>;
}
