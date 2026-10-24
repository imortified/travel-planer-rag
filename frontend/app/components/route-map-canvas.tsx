"use client";

import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import type { StyleSpecification } from "maplibre-gl";
import { clock } from "../../lib/api";
import type { MapDay } from "../../lib/route-map";

maplibregl.setWorkerUrl(new URL("maplibre-gl/dist/maplibre-gl-worker.mjs",import.meta.url).toString());

export default function RouteMapCanvas({days,active,onSelect,basemap}:{days:MapDay[];active:{key:string}|null;onSelect:(key:string)=>void;basemap:boolean}) {
  const element=useRef<HTMLDivElement>(null);
  const mapRef=useRef<maplibregl.Map|null>(null);
  const readyMap=useRef<maplibregl.Map|null>(null);
  const markers=useRef(new Map<string,maplibregl.Marker>());
  const selection=useRef(onSelect);
  selection.current=onSelect;
  const [error,setError]=useState("");
  const [tilesError,setTilesError]=useState(false);
  const [loaded,setLoaded]=useState(0);
  useEffect(()=>{
    if(!element.current) return;
    let map:maplibregl.Map;
    const style:StyleSpecification={version:8,sources:{},layers:[{id:"background",type:"background",paint:{"background-color":"#edf0e8"}}]};
    try {
      map=new maplibregl.Map({container:element.current,style,center:[0,0],zoom:1,maxZoom:18,attributionControl:false,renderWorldCopies:false,dragRotate:false,pitchWithRotate:false,locale:{"NavigationControl.ZoomIn":"Приблизить","NavigationControl.ZoomOut":"Отдалить","Popup.Close":"Закрыть"}});
    } catch {
      setError("Браузер не смог открыть карту. Все посещения и расписание доступны в списке.");
      return;
    }
    mapRef.current=map;
    map.addControl(new maplibregl.NavigationControl({showCompass:false}),"top-right");
    map.addControl(new maplibregl.AttributionControl({compact:false}),"bottom-right");
    map.scrollZoom.disable();
    map.getCanvas().setAttribute("aria-label","Карта мест поездки. Стрелки — перемещение, плюс и минус — масштаб.");
    const positions:[number,number][]=[];
    const starts=new Set<string>();
    const pins:maplibregl.Marker[]=[];
    map.on("error",e=>{
      if("sourceId" in e && e.sourceId==="osm") setTilesError(true);
      else setError("Карта временно недоступна. Все посещения и расписание доступны в списке.");
    });
    const lost=(event:Event)=>{event.preventDefault();setError("Карта временно недоступна. Продолжайте по списку посещений.");};
    map.getCanvas().addEventListener("webglcontextlost",lost);
    map.on("load",()=>{
      const features=days.flatMap(d=>d.segments.map(coordinates=>({type:"Feature" as const,properties:{color:d.color},geometry:{type:"LineString" as const,coordinates}})));
      map.addSource("sequence",{type:"geojson",data:{type:"FeatureCollection",features}});
      map.addLayer({id:"sequence-outline",type:"line",source:"sequence",paint:{"line-color":"#fffefb","line-width":7,"line-opacity":0.9}});
      map.addLayer({id:"sequence",type:"line",source:"sequence",paint:{"line-color":["get","color"],"line-width":3.5,"line-dasharray":[2,2]}});
      for(const day of days) {
        if(day.stops.length && day.origin && !starts.has(day.origin.coordinates.join(","))) {
          starts.add(day.origin.coordinates.join(","));
          positions.push(day.origin.coordinates);
          const pin=document.createElement("button");pin.type="button";pin.className="map-origin";pin.textContent="С";pin.setAttribute("aria-label",`Начало дня: ${day.origin.name}`);
          const popup=new maplibregl.Popup({offset:18}).setText(`Начало дня: ${day.origin.name}`);
          const marker=new maplibregl.Marker({element:pin}).setLngLat(day.origin.coordinates).setPopup(popup).addTo(map);pins.push(marker);
        }
        for(const stop of day.stops) {
          if(!stop.coordinates) continue;
          positions.push(stop.coordinates);
          const pin=document.createElement("button");pin.type="button";pin.className="map-pin";pin.style.background=day.color;pin.textContent=`${day.day}·${stop.order}`;
          pin.setAttribute("aria-label",`День ${day.day}, место ${stop.order}: ${stop.name}`);
          pin.addEventListener("click",event=>{event.stopPropagation();selection.current(stop.key);});
          const popup=new maplibregl.Popup({offset:18}).setText(`День ${day.day} · ${stop.order}. ${stop.name}. ${clock(stop.start)}–${clock(stop.end)}`);
          const marker=new maplibregl.Marker({element:pin}).setLngLat(stop.coordinates).setPopup(popup).addTo(map);pins.push(marker);markers.current.set(stop.key,marker);
        }
      }
      if(positions.length) {
        const bounds=positions.reduce((b,p)=>b.extend(p),new maplibregl.LngLatBounds(positions[0],positions[0]));
        map.fitBounds(bounds,{padding:45,maxZoom:15,duration:0});
      }
      readyMap.current=map;
      setLoaded(value=>value+1);
    });
    const observer=new ResizeObserver(()=>map.resize());observer.observe(element.current);
    return ()=>{observer.disconnect();map.getCanvas().removeEventListener("webglcontextlost",lost);pins.forEach(p=>p.remove());markers.current.clear();map.remove();mapRef.current=null;readyMap.current=null;};
  },[days]);
  useEffect(()=>{
    const map=mapRef.current;if(!map || map!==readyMap.current) return;
    const apply=()=>{
      if(!map.getSource("osm")) {
        map.addSource("osm",{type:"raster",tiles:["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],tileSize:256,maxzoom:19,attribution:'© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap contributors</a>'});
        map.addLayer({id:"osm",type:"raster",source:"osm",layout:{visibility:basemap?"visible":"none"}},"sequence-outline");
      } else map.setLayoutProperty("osm","visibility",basemap?"visible":"none");
      if(basemap) setTilesError(false);
    };
    apply();
  },[basemap,days,loaded]);
  useEffect(()=>{
    const map=mapRef.current;if(!map || map!==readyMap.current || !active) return;
    const marker=markers.current.get(active.key);if(!marker) return;
    markers.current.forEach((m,key)=>{if(key!==active.key && m.getPopup()?.isOpen()) m.togglePopup();});
    map.easeTo({center:marker.getLngLat(),duration:0});
    if(!marker.getPopup()?.isOpen()) marker.togglePopup();
  },[active,loaded]);
  return <><div ref={element} className={`map-canvas${error?" map-unavailable":""}`} aria-label="Географическая карта маршрута"/>{error && <p className="hint" role="status">{error}</p>}{basemap && tilesError && <p className="hint" role="status">Подложка OpenStreetMap не загрузилась. Точки и порядок посещений остаются доступны; подложку можно выключить.</p>}</>;
}
