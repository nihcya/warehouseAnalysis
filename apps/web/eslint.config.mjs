import { FlatCompat } from "@eslint/eslintrc";

const compat = new FlatCompat({
  baseDirectory: import.meta.dirname,
});

const eslintConfig = [
  {
    // src/api/types/** 为 openapi-typescript 生成产物；next-env.d.ts 由 Next 自动生成
    ignores: [".next/**", "node_modules/**", "src/api/types/**", "next-env.d.ts"],
  },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
];

export default eslintConfig;
