with Arthexis.ORM;

package Apps.Migrations.Functions.Migrations_Functions is
   --  SQL-callable function registrations for the Migrations app.

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Register migration state views and seed baseline rows.
end Apps.Migrations.Functions.Migrations_Functions;
