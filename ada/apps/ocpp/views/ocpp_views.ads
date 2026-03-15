package Apps.OCPP.Views.OCPP_Views is
   """Read-side view contracts for OCPP app consumers."""

   function Active_Transactions_SQL return String;
   --  Query used by dashboards and RPC endpoints.
end Apps.OCPP.Views.OCPP_Views;
