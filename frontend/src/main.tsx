import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import "./styles/theme.css";
import "./styles/tailwind.css";
import HomePage from "./pages/HomePage";
import TerminalPage from "./pages/TerminalPage";
import StrategiesPage from "./pages/StrategiesPage";
import BacktestPage from "./pages/BacktestPage";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/terminal" element={<TerminalPage />} />
        <Route path="/terminal/:symbol" element={<TerminalPage />} />
        <Route path="/strategies" element={<StrategiesPage />} />
        <Route path="/backtest" element={<BacktestPage />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
