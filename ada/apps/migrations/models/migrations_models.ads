with Arthexis.ORM;

package Apps.Migrations.Models.Migrations_Models is
   --  Schema objects owned by the Migrations app.

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Create migration metadata and execution tables.
end Apps.Migrations.Models.Migrations_Models;
