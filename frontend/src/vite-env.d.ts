/// <reference types="vite/client" />

// TS 5.6 打开 noUncheckedSideEffectImports 后，纯副作用的 CSS import
// （例如 `import "@/index.css"`）必须能解析到模块类型声明。
// Vite 自己只给 *.module.css 做了类型，这里补上裸 CSS / 图片等常见资源。
declare module "*.css";
declare module "*.scss";
declare module "*.svg" {
  const src: string;
  export default src;
}
declare module "*.png" {
  const src: string;
  export default src;
}
declare module "*.jpg" {
  const src: string;
  export default src;
}
