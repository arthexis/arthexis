package Apps.OCPP.Views.OCPP_Views is
   --  Read-side view contracts for OCPP app consumers.

   function Active_Transactions_SQL return String;
   --  Query used by dashboards and RPC endpoints.

   function CLI_Simulator_Targets_SQL return String;
   --  Query used by CLI simulators to resolve charger connection targets.

   function Charger_Web_Status_SQL return String;
   --  Query used by the web interface product entry point.
end Apps.OCPP.Views.OCPP_Views;
