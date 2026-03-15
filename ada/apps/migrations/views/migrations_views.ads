package Apps.Migrations.Views.Migrations_Views is
   --  Migrations app view contracts for Ada adapters.

   function Pending_Migrations_SQL return String;
   --  SQL projection for pending migration introspection.
end Apps.Migrations.Views.Migrations_Views;
